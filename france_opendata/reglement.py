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

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Union

import requests

_UA = "france-opendata/reglement"
DEFAULT_TIMEOUT = 600
MIN_TEXT_CHARS = 2000  # en-dessous : PDF probablement scanné (image), illisible sans OCR

# Exceptions réseau transitoires → on reprend ; les erreurs HTTP (4xx/5xx) propagent direct.
_RETRYABLE = (requests.ConnectionError, requests.Timeout,
              requests.exceptions.ChunkedEncodingError)


class PdftotextManquant(RuntimeError):
    """poppler-utils (`pdftotext`) absent du système."""


def _require_pdftotext() -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        raise PdftotextManquant(
            "pdftotext introuvable — installer poppler-utils "
            "(Debian/Ubuntu : `apt install poppler-utils`).")
    return exe


def download_pdf(url: str, dest: Union[str, Path], *, max_attempts: int = 6,
                 timeout: int = DEFAULT_TIMEOUT, verbose: bool = False) -> Path:
    """Télécharge un PDF en **reprise sur erreur** (`Range: bytes=`) — data.geopf.fr coupe
    souvent les gros transferts. Reprend depuis l'octet déjà écrit. Renvoie le chemin."""
    dest = Path(dest)
    pos = 0
    for attempt in range(max_attempts):
        headers = {"User-Agent": _UA}
        if pos:
            headers["Range"] = f"bytes={pos}-"
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
                r.raise_for_status()  # 4xx/5xx → propage (pas de reprise inutile)
                # Range demandé mais 200 (serveur ignore le Range) → on repart de 0.
                resume = bool(pos) and r.status_code == 206
                if pos and not resume:
                    pos = 0
                with open(dest, "ab" if resume else "wb") as fh:
                    if not resume:
                        fh.seek(0)
                        fh.truncate()
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
                        pos += len(chunk)
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


def parse_pdf(source: Union[str, Path, bytes]) -> dict:
    """Extrait le texte d'un PDF de règlement via `pdftotext -layout`.

    `source` = chemin du PDF ou contenu en bytes. Renvoie `{text, chars, scanne_probable}` ;
    `scanne_probable`=True si < `MIN_TEXT_CHARS` extraits (PDF image, illisible sans OCR)."""
    exe = _require_pdftotext()
    if isinstance(source, (bytes, bytearray)):
        proc = subprocess.run([exe, "-layout", "-", "-"], input=bytes(source),
                              capture_output=True, check=True)
    else:
        proc = subprocess.run([exe, "-layout", str(source), "-"],
                              capture_output=True, check=True)
    text = proc.stdout.decode("utf-8", errors="replace")
    chars = len(text)
    return {"text": text, "chars": chars, "scanne_probable": chars < MIN_TEXT_CHARS}


def fetch_and_parse(url: str, *, timeout: int = DEFAULT_TIMEOUT, verbose: bool = False) -> dict:
    """Télécharge le PDF de règlement et en extrait le texte (download résumable + pdftotext).

    Renvoie `{text, chars, scanne_probable, size_mo}`. Pratique pour un pipeline d'ingestion."""
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "reglement.pdf"
        download_pdf(url, pdf, timeout=timeout, verbose=verbose)
        size_mo = pdf.stat().st_size / 1e6
        out = parse_pdf(pdf)
    out["size_mo"] = round(size_mo, 1)
    return out
