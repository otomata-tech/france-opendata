"""Crawl du dump XML DILA LEGI → itérateur de versions d'articles (parsées par `legi`).

Même modèle qu'ACCO/KALI :
  - `Freemium_legi_global_*.tar.gz` (~1,2 Go) : **stock complet** ;
  - `LEGI_YYYYMMDD-HHMMSS.tar.gz` : **incréments quotidiens** (~12 mois glissants).

Périmètre : les **articles des codes en vigueur** uniquement (chemin
`code_et_TNC_en_vigueur/code_en_vigueur/**/article/**` — historique des versions
INCLUS : « en vigueur » qualifie le code, pas la version). Les textes non
codifiés (TNC) relèvent de la capacité JORF (phase 3, service#8).

`rows_from_archive` produit les dicts articles ; le stockage est au consommateur
(france-opendata-service#7). Nécessite l'extra `france-opendata[stock]`.
"""
from __future__ import annotations

import re
import tarfile
from typing import Any, Iterator, Optional

import requests

from .legi import parse_legi_article

BASE_URL = "https://echanges.dila.gouv.fr/OPENDATA/LEGI"
GLOBAL_NAME = "Freemium_legi_global_20250713-140000.tar.gz"

_ARCHIVE_RE = re.compile(r"LEGI_\d{8}-\d{6}\.tar\.gz")
_ARTICLE_RE = re.compile(r"code_et_TNC_en_vigueur/code_en_vigueur/.*article/.*LEGIARTI\d+\.xml$")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "france-opendata/legi-ingest (+https://github.com/otomata-tech/france-opendata)"
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
        day = n.split("_")[1][:8]  # LEGI_YYYYMMDD-...
        if since_compact and day < since_compact:
            continue
        out.append(f"{BASE_URL}/{n}")
    return out


def _rows_from_tar_stream(fileobj, limit: Optional[int] = None) -> Iterator[dict[str, Any]]:
    """Itère les versions d'articles d'un flux tar.gz (codes en vigueur uniquement)."""
    n = 0
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:  # r|gz = streaming, mono-passe
        for member in tar:
            if not member.isfile() or not _ARTICLE_RE.search(member.name):
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            row = parse_legi_article(f.read())
            if row is not None:
                yield row
                n += 1
                if limit and n >= limit:
                    return


def rows_from_archive(url_or_path: str, sess: Optional[requests.Session] = None,
                      limit: Optional[int] = None) -> Iterator[dict[str, Any]]:
    """Itère les articles d'une archive tar.gz, locale (chemin) ou distante (URL, streamée)."""
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        sess = sess or _session()
        with sess.get(url_or_path, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            resp.raw.decode_content = True
            yield from _rows_from_tar_stream(resp.raw, limit=limit)
    else:
        with open(url_or_path, "rb") as fh:
            yield from _rows_from_tar_stream(fh, limit=limit)
