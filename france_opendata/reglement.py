"""Règlement PLU/PLUi — téléchargement + extraction texte d'un PDF de règlement.

Util **pur** (comme `geo`, `finance`, `epfif.parse`) : pas d'état, pas de client. Sert à
construire un cache de règlements indexé par `idurba` — le consommateur décide où il stocke.

Le règlement écrit d'un PLU/PLUi n'est pas en open data structuré : c'est un PDF (souvent
>100 Mo, intercommunal) publié sur data.geopf.fr, dont l'URL se résout via
`GpuClient.reglement_url`. On le télécharge (**résumable** — data.geopf.fr coupe souvent les
gros transferts) et on en extrait le texte via `pdftotext -layout` (poppler-utils, **binaire
système requis** ; pas de fallback silencieux).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Union

import requests

_UA = "france-opendata/reglement"
DEFAULT_TIMEOUT = 600
MIN_TEXT_CHARS = 2000  # en-dessous : PDF probablement scanné (image), illisible sans OCR

# `pdftotext -layout` reproduit la mise en page spatiale du PDF : pour un PLUi
# intercommunal (sommaires à points de suite + colonnes alignées au padding), ça
# gonfle le texte d'un facteur ~40× (98% d'espaces de remplissage), sans ajouter de
# contenu. On dégonfle ligne-à-ligne — en GARDANT les `\n`, car les extraits sont
# recherchés ligne par ligne côté consommateur.
_DOTS_LEADERS = re.compile(r"\.{4,}")      # points de suite « …… » des sommaires
_RUN_SPACES = re.compile(r"[ \t]{2,}")     # padding d'alignement -layout
_RUN_BLANKS = re.compile(r"\n{3,}")        # rafales de lignes vides


def _normalize_layout(text: str) -> str:
    """Dégonfle la sortie `pdftotext -layout` (cf. note ci-dessus). Idempotent."""
    lines = (
        _RUN_SPACES.sub(" ", _DOTS_LEADERS.sub("…", line)).strip()
        for line in text.split("\n")
    )
    return _RUN_BLANKS.sub("\n\n", "\n".join(lines))

# Exceptions réseau transitoires → on reprend ; les erreurs HTTP (4xx/5xx) propagent direct.
_RETRYABLE = (requests.ConnectionError, requests.Timeout,
              requests.exceptions.ChunkedEncodingError)


class PdftotextManquant(RuntimeError):
    """poppler-utils (`pdftotext`) absent du système."""


class ReglementTropVolumineux(RuntimeError):
    """Le PDF dépasse la taille admise par l'appelant (`max_bytes`)."""


class DelaiDepasse(TimeoutError):
    """Téléchargement + extraction n'ont pas tenu dans le délai donné (`delai_s`)."""


class PasUnPdf(ValueError):
    """La ressource téléchargée n'est pas un PDF (page d'erreur servie en 200…)."""


def _restant(echeance: float | None) -> float | None:
    """Secondes restantes avant `echeance` (horloge monotone) ; lève si dépassée."""
    if echeance is None:
        return None
    reste = echeance - time.monotonic()
    if reste <= 0:
        raise DelaiDepasse("délai d'ingestion dépassé")
    return reste


def _require_pdftotext() -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        raise PdftotextManquant(
            "pdftotext introuvable — installer poppler-utils "
            "(Debian/Ubuntu : `apt install poppler-utils`).")
    return exe


def download_pdf(url: str, dest: Union[str, Path], *, max_attempts: int = 6,
                 timeout: int = DEFAULT_TIMEOUT, verbose: bool = False,
                 max_bytes: int | None = None, echeance: float | None = None) -> Path:
    """Télécharge un PDF en **reprise sur erreur** (`Range: bytes=`) — data.geopf.fr coupe
    souvent les gros transferts. Reprend depuis l'octet déjà écrit. Renvoie le chemin.

    Garde-fous optionnels : `max_bytes` (refus dès l'en-tête `Content-Length`, sinon au
    fil du flux → `ReglementTropVolumineux`) et `echeance` (instant `time.monotonic()`
    au-delà duquel on abandonne → `DelaiDepasse`). Un contenu qui ne commence pas par
    `%PDF` lève `PasUnPdf`."""
    dest = Path(dest)
    pos = 0
    for attempt in range(max_attempts):
        headers = {"User-Agent": _UA}
        if pos:
            headers["Range"] = f"bytes={pos}-"
        reste = _restant(echeance)
        try:
            with requests.get(url, headers=headers, stream=True,
                              timeout=min(timeout, reste) if reste else timeout) as r:
                r.raise_for_status()  # 4xx/5xx → propage (pas de reprise inutile)
                # Range demandé mais 200 (serveur ignore le Range) → on repart de 0.
                resume = bool(pos) and r.status_code == 206
                if pos and not resume:
                    pos = 0
                annonce = int(r.headers.get("Content-Length") or 0) + pos
                if max_bytes and annonce > max_bytes:
                    raise ReglementTropVolumineux(
                        f"PDF de {annonce / 1e6:.0f} Mo, au-delà de {max_bytes / 1e6:.0f} Mo admis")
                with open(dest, "ab" if resume else "wb") as fh:
                    if not resume:
                        fh.seek(0)
                        fh.truncate()
                    for chunk in r.iter_content(1 << 20):
                        if not pos and not chunk.startswith(b"%PDF"):
                            raise PasUnPdf("la ressource téléchargée n'est pas un PDF")
                        fh.write(chunk)
                        pos += len(chunk)
                        if max_bytes and pos > max_bytes:
                            raise ReglementTropVolumineux(
                                f"PDF au-delà de {max_bytes / 1e6:.0f} Mo admis")
                        _restant(echeance)
            return dest
        except _RETRYABLE as e:
            pos = dest.stat().st_size if dest.exists() else 0
            if verbose:
                print(f"  reprise après {type(e).__name__} à {pos/1e6:.1f} Mo "
                      f"(tentative {attempt + 2}/{max_attempts})")
            if attempt == max_attempts - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("download_pdf: boucle de reprise épuisée")  # garde-fou


def parse_pdf(source: Union[str, Path, bytes], *, timeout: float | None = None) -> dict:
    """Extrait le texte d'un PDF de règlement via `pdftotext -layout`, puis dégonfle la
    mise en page (`_normalize_layout` : points de suite, padding, lignes vides en rafale).

    `source` = chemin du PDF ou contenu en bytes. Renvoie `{text, chars, scanne_probable}` ;
    `scanne_probable`=True si < `MIN_TEXT_CHARS` extraits (PDF image, illisible sans OCR).
    `timeout` (s) borne `pdftotext` → `DelaiDepasse`."""
    exe = _require_pdftotext()
    args = [exe, "-layout", "-" if isinstance(source, (bytes, bytearray)) else str(source), "-"]
    try:
        proc = subprocess.run(args, input=bytes(source) if isinstance(source, (bytes, bytearray)) else None,
                              capture_output=True, check=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise DelaiDepasse("extraction pdftotext trop longue") from e
    text = _normalize_layout(proc.stdout.decode("utf-8", errors="replace"))
    chars = len(text)
    return {"text": text, "chars": chars, "scanne_probable": chars < MIN_TEXT_CHARS}


def fetch_and_parse(url: str, *, timeout: int = DEFAULT_TIMEOUT, verbose: bool = False,
                    max_bytes: int | None = None, echeance: float | None = None) -> dict:
    """Télécharge le PDF de règlement et en extrait le texte (download résumable + pdftotext).

    Renvoie `{text, chars, scanne_probable, size_mo}`. Pratique pour un pipeline d'ingestion.
    `max_bytes` / `echeance` : cf. `download_pdf` (l'échéance borne aussi `pdftotext`)."""
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "reglement.pdf"
        download_pdf(url, pdf, timeout=timeout, verbose=verbose,
                     max_bytes=max_bytes, echeance=echeance)
        size_mo = pdf.stat().st_size / 1e6
        out = parse_pdf(pdf, timeout=_restant(echeance))
    out["size_mo"] = round(size_mo, 1)
    return out


def fetch_and_parse_all(urls: list[str], *, max_bytes: int | None = None,
                        delai_s: float | None = None, verbose: bool = False) -> dict:
    """Règlement écrit publié en une ou plusieurs pièces : chaque PDF est téléchargé et
    extrait, les textes sont mis bout à bout (une ligne `=== <fichier> ===` les sépare
    quand il y en a plusieurs). `max_bytes` vaut par fichier, `delai_s` pour l'ensemble.

    Renvoie `{text, chars, scanne_probable, size_mo, fichiers}` ; `scanne_probable` est
    vrai si UNE pièce l'est (son texte manque, le règlement n'est pas lisible en entier),
    `fichiers` détaille chaque pièce `{url, chars, scanne_probable, size_mo}`."""
    if not urls:
        raise ValueError("fetch_and_parse_all : aucune URL")
    echeance = time.monotonic() + delai_s if delai_s else None
    parts, fichiers = [], []
    for url in urls:
        r = fetch_and_parse(url, verbose=verbose, max_bytes=max_bytes, echeance=echeance)
        fichiers.append({"url": url, "chars": r["chars"],
                         "scanne_probable": r["scanne_probable"], "size_mo": r["size_mo"]})
        parts.append(r["text"] if len(urls) == 1 else f"=== {url.rsplit('/', 1)[-1]} ===\n{r['text']}")
    text = "\n\n".join(parts)
    return {"text": text, "chars": len(text),
            "scanne_probable": any(f["scanne_probable"] for f in fichiers),
            "size_mo": round(sum(f["size_mo"] for f in fichiers), 1), "fichiers": fichiers}


# --- lecture : chapitre d'une zone, passages ---------------------------------
#
# Un règlement écrit est découpé en chapitres par zone (« ZONE UA », « Zone UCe1 »,
# « CHAPITRE 2 – DISPOSITIONS APPLICABLES À LA ZONE UB ») ; les règles d'un secteur
# (UAa, UCe1b) vivent dans le chapitre de sa zone. Les mots-clés du titre sont
# insensibles à la casse, le code de zone ne l'est pas (`UCe1b` ≠ `UCE1B` ≠ `uce1b`) ;
# en tête de ligne, « Zone » s'écrit avec sa capitale (« zone A ou N du fait… » est une
# phrase coupée), après « Dispositions applicables à la » il est libre.
# L'espacement de `pdftotext` sépare parfois l'indice (« Zone A 2 » pour A2).
_TITRE_ZONE = re.compile(
    r"^(?:(?i:chapitre|titre|partie|section)\s+[\w.]+\s*[-–—:.]?\s*)?"
    r"(?:(?i:(?:dispositions|r[èe]gles?)\s+(?:applicables|sp[ée]cifiques)\s+(?:[àa]\s+la|aux?)\s+zones?)"
    r"|ZONES?|Zones?)\s+(?P<code>\d*[A-Z][0-9A-Za-z]*(?: \d{1,2}[a-z]?)?)(?![0-9A-Za-zÀ-ÿ])")
_TITRE_MAX_CHARS = 100  # au-delà, la ligne est une phrase qui commence par « Zone UA… »


def _titres_zone(lines: list[str]) -> list[tuple[int, str]]:
    """(index de ligne, code) des titres de chapitre de zone, hors sommaire (`…`)."""
    out = []
    for i, line in enumerate(lines):
        if len(line) > _TITRE_MAX_CHARS or "…" in line:
            continue
        m = _TITRE_ZONE.match(line)
        if m:
            out.append((i, m["code"].replace(" ", "")))
    return out


def section_zone(lines: list[str], zone: str) -> dict | None:
    """Chapitre du règlement qui porte la zone `zone`, ou celui de sa zone mère (`UAa`
    → `UA`, `UCe1b` → `UCe1`) quand le secteur n'a pas de chapitre propre.

    Renvoie `{zone_demandee, zone_chapitre, titre, ligne_debut, ligne_fin}` (fin
    exclusive) ou None si aucun titre de chapitre ne porte la zone ni une zone mère —
    la mise en page du règlement n'a pas été reconnue, rien n'est deviné. Un chapitre
    court jusqu'au titre d'une autre zone (pas une de ses sous-zones) ; parmi plusieurs
    titres du même code (sommaire sans points de suite, rappels), le plus long l'emporte."""
    titres = _titres_zone(lines)
    zone = zone.strip()
    for n in range(len(zone), 0, -1):
        code = zone[:n]
        if not any(c.isalpha() for c in code):
            break
        best = None
        for k, (debut, c) in enumerate(titres):
            if c != code:
                continue
            fin = next((i for i, c2 in titres[k + 1:] if not c2.startswith(code)), len(lines))
            if best is None or fin - debut > best[1] - best[0]:
                best = (debut, fin)
        if best:
            return {"zone_demandee": zone, "zone_chapitre": code, "titre": lines[best[0]],
                    "ligne_debut": best[0], "ligne_fin": best[1]}
    return None


def _plie(s: str) -> str:
    """Casse et accents repliés : « Hauteur maximale » trouve « HAUTEUR MAXIMALE »."""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").casefold()


def passages(lines: list[str], *, query: str | None = None, zone: str | None = None,
             debut: int = 0, fin: int | None = None, max_extraits: int = 8,
             context_lignes: int = 30) -> tuple[int, list[str]]:
    """Passages autour des lignes qui contiennent `query` (casse et accents repliés) ou
    le code `zone` (mot entier, casse exacte), entre les lignes `debut` et `fin`.

    Un passage = 3 lignes avant, `context_lignes` après ; une occurrence déjà couverte
    par le passage précédent n'en ouvre pas un nouveau. Renvoie (nb d'occurrences,
    passages)."""
    fin = len(lines) if fin is None else fin
    if query:
        q = _plie(query)
        test = lambda ln: q in _plie(ln)  # noqa: E731
    elif zone:
        rz = re.compile(r"(?<![0-9A-Za-zÀ-ÿ])" + re.escape(zone.strip()) + r"(?![0-9A-Za-zÀ-ÿ])")
        test = rz.search
    else:
        raise ValueError("passages : `query` ou `zone` requis")
    hits = [i for i in range(debut, fin) if test(lines[i])]
    out, couvert = [], -1
    for i in hits:
        if i < couvert:
            continue
        a, b = max(debut, i - 3), min(fin, i + context_lignes)
        out.append("\n".join(lines[a:b]))
        couvert = b
        if len(out) >= max_extraits:
            break
    return len(hits), out
