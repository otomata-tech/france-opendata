"""Crawl du dump XML DILA KALI → itérateur de lignes typées (parsées par `kali`).

Le dump KALI de la DILA (`echanges.dila.gouv.fr/OPENDATA/KALI/`) suit le même modèle
qu'ACCO :
  - `Freemium_kali_global_*.tar.gz` (~175 Mo) : **stock complet** — conteneurs,
    textes, sections et articles ;
  - `KALI_YYYYMMDD-HHMMSS.tar.gz` (~0,1 Mo) : **incréments quotidiens** (~12 mois
    glissants conservés en ligne).

Une archive mélange les types d'objets ; en streaming mono-passe l'ordre
conteneur/article n'est pas garanti → `rows_from_archive` produit des lignes
**typées** `("conteneur", dict)` / `("article", dict)` et laisse la jointure
article→IDCC au stockage (SQL, `conteneur_id`). oto ne consomme pas ce module
directement : l'ingestion vit dans france-opendata-service.

Nécessite l'extra `france-opendata[stock]` (defusedxml).
"""
from __future__ import annotations

import re
import tarfile
from typing import Any, Iterator, Optional

import requests

from .kali import parse_kali_article, parse_kali_conteneur

BASE_URL = "https://echanges.dila.gouv.fr/OPENDATA/KALI"
GLOBAL_NAME = "Freemium_kali_global_20250713-140000.tar.gz"

_ARCHIVE_RE = re.compile(r"KALI_\d{8}-\d{6}\.tar\.gz")
_CONTENEUR_RE = re.compile(r"conteneur/.*KALICONT\d+\.xml$")
_ARTICLE_RE = re.compile(r"article/.*KALIARTI\d+\.xml$")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "france-opendata/kali-ingest (+https://github.com/otomata-tech/france-opendata)"
    return s


def list_daily_archives(sess: Optional[requests.Session] = None, since: Optional[str] = None) -> list[str]:
    """URLs des archives quotidiennes en ligne (triées), filtrées par date >= `since` (YYYY-MM-DD)."""
    sess = sess or _session()
    resp = sess.get(f"{BASE_URL}/", timeout=60)
    resp.raise_for_status()
    names = sorted(set(_ARCHIVE_RE.findall(resp.text)))
    since_compact = since.replace("-", "") if since else None
    out = []
    for n in names:
        day = n.split("_")[1][:8]  # KALI_YYYYMMDD-...
        if since_compact and day < since_compact:
            continue
        out.append(f"{BASE_URL}/{n}")
    return out


def _rows_from_tar_stream(fileobj, limit: Optional[int] = None) -> Iterator[tuple[str, dict[str, Any]]]:
    """Itère les objets KALI d'un flux tar.gz en lignes typées ("conteneur"|"article", dict)."""
    n = 0
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:  # r|gz = streaming, mono-passe
        for member in tar:
            if not member.isfile():
                continue
            if _CONTENEUR_RE.search(member.name):
                kind, parse = "conteneur", parse_kali_conteneur
            elif _ARTICLE_RE.search(member.name):
                kind, parse = "article", parse_kali_article
            else:
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            row = parse(f.read())
            if row is not None:
                yield kind, row
                n += 1
                if limit and n >= limit:
                    return


def rows_from_archive(url_or_path: str, sess: Optional[requests.Session] = None,
                      limit: Optional[int] = None) -> Iterator[tuple[str, dict[str, Any]]]:
    """Itère les objets d'une archive tar.gz, locale (chemin) ou distante (URL, streamée)."""
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        sess = sess or _session()
        with sess.get(url_or_path, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            resp.raw.decode_content = True
            yield from _rows_from_tar_stream(resp.raw, limit=limit)
    else:
        with open(url_or_path, "rb") as fh:
            yield from _rows_from_tar_stream(fh, limit=limit)
