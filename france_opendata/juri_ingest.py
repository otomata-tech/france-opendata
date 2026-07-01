"""Crawl des dumps XML DILA de jurisprudence → itérateur de décisions (par fond).

Six fonds, même modèle (global + quotidiens, cf. `juri`). `FONDS` porte la
config par fond (URL, nom du global) ; toutes les fonctions prennent le fond en
premier argument. Volumétrie des globaux (2025-07) : JADE 1,24 Go, INCA 0,69 Go,
CAPP 0,29 Go, CASS 0,26 Go, CNIL 0,02 Go, CONSTIT 0,01 Go.

Nécessite l'extra `france-opendata[stock]` (defusedxml).
"""
from __future__ import annotations

import re
import tarfile
from typing import Any, Iterator, Optional

import requests

from .juri import parse_juri_decision

# fond → nom du dump global (les quotidiens suivent le motif <FOND>_YYYYMMDD-HHMMSS.tar.gz)
FONDS: dict[str, str] = {
    "cass":    "Freemium_cass_global_20250713-140000.tar.gz",
    "inca":    "Freemium_inca_global_20250713-140000.tar.gz",
    "capp":    "Freemium_capp_global_20250713-140000.tar.gz",
    "jade":    "Freemium_jade_global_20250713-140000.tar.gz",
    "constit": "Freemium_constit_global_20250713-140000.tar.gz",
    "cnil":    "Freemium_cnil_global_20250713-140000.tar.gz",
}


def base_url(fond: str) -> str:
    if fond not in FONDS:
        raise ValueError(f"fond inconnu : {fond} (attendu : {', '.join(FONDS)})")
    return f"https://echanges.dila.gouv.fr/OPENDATA/{fond.upper()}"


def global_url(fond: str) -> str:
    return f"{base_url(fond)}/{FONDS[fond]}"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "france-opendata/juri-ingest (+https://github.com/otomata-tech/france-opendata)"
    return s


def list_daily_archives(fond: str, sess: Optional[requests.Session] = None,
                        since: Optional[str] = None) -> list[str]:
    """URLs des archives quotidiennes en ligne (triées), filtrées par date >= `since`."""
    sess = sess or _session()
    resp = sess.get(f"{base_url(fond)}/", timeout=60)
    resp.raise_for_status()
    pattern = re.compile(rf"{fond.upper()}_\d{{8}}-\d{{6}}\.tar\.gz")
    names = sorted(set(pattern.findall(resp.text)))
    since_compact = since.replace("-", "") if since else None
    out = []
    for n in names:
        day = n.split("_")[1][:8]
        if since_compact and day < since_compact:
            continue
        out.append(f"{base_url(fond)}/{n}")
    return out


def _rows_from_tar_stream(fileobj, limit: Optional[int] = None) -> Iterator[dict[str, Any]]:
    """Itère les décisions d'un flux tar.gz (tout .xml — une archive = un seul fond)."""
    n = 0
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:  # r|gz = streaming, mono-passe
        for member in tar:
            if not member.isfile() or not member.name.endswith(".xml"):
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            row = parse_juri_decision(f.read())
            if row is not None:
                yield row
                n += 1
                if limit and n >= limit:
                    return


def rows_from_archive(url_or_path: str, sess: Optional[requests.Session] = None,
                      limit: Optional[int] = None) -> Iterator[dict[str, Any]]:
    """Itère les décisions d'une archive tar.gz, locale (chemin) ou distante (URL, streamée)."""
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        sess = sess or _session()
        with sess.get(url_or_path, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            resp.raw.decode_content = True
            yield from _rows_from_tar_stream(resp.raw, limit=limit)
    else:
        with open(url_or_path, "rb") as fh:
            yield from _rows_from_tar_stream(fh, limit=limit)
