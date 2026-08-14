"""Crawl du dump XML DILA ACCO → itérateur de lignes (parsées par `acco.parse_acco`).

Le dump ACCO de la DILA (`echanges.dila.gouv.fr/OPENDATA/ACCO/`) se présente en
archives **tar.gz**, pas en arborescence de fichiers comme BOAMP :
  - `Freemium_acco_global_*.tar.gz` (~45 Go) : **stock complet** depuis 2017 — bundle
    les .docx ; on n'extrait QUE les XML métadonnées (`*/TEXT/**/ACCOTEXT*.xml`).
  - `ACCO_YYYYMMDD-HHMMSS.tar.gz` (~80 Mo) : **incréments hebdo** (~12 mois glissants
    conservés en ligne).

Ce module ne fait QUE produire des dicts de lignes (`rows_from_archive`) ; le stockage
est au consommateur. oto-backend les upsert en PostgreSQL (`deploy/ingest_acco.py` →
`db.upsert_acco`, idempotent par `id`) : bootstrap = global streamé (jamais sur disque)
puis tous les hebdo ; maintenance = un hebdo récent.

Nécessite l'extra `france-opendata[stock]` (defusedxml, pour `parse_acco`).
"""
from __future__ import annotations

import re
import tarfile
from typing import Any, Iterator, Optional

import requests

from .acco import parse_acco

BASE_URL = "https://echanges.dila.gouv.fr/OPENDATA/ACCO"
GLOBAL_NAME = "Freemium_acco_global_20250713-140000.tar.gz"

_ARCHIVE_RE = re.compile(r"ACCO_\d{8}-\d{6}\.tar\.gz")
_MEMBER_RE = re.compile(r"TEXT/.*ACCOTEXT\d+\.xml$")
# `…_YYYYMMDD-HHMMSS.tar.gz` — vaut pour les hebdo comme pour le global Freemium.
_ARCHIVE_DATE_RE = re.compile(r"_(\d{4})(\d{2})(\d{2})-\d{6}\.tar\.gz$")

# Ce que la DILA impose de mentionner à qui rediffuse ces données (fiche officielle
# `DILA_ACCO_Presentation_20171212.pdf`, servie à la racine du dump) : « Les données
# sont réutilisables gratuitement sous licence ouverte v2.0. Les réutilisateurs
# s'obligent à mentionner : la paternité des données (DILA) ; l'URL d'accès longue de
# téléchargement ; le nom du fichier téléchargé ainsi que la date du fichier. »
#
# Deux des trois mentions se rapportent à l'ARCHIVE d'origine, pas au jeu de données :
# elles sont donc à capter ICI, à l'ingestion, ou perdues pour toujours. C'est ce qui
# justifie `source_archive_url` / `source_archive_date` sur chaque ligne.
LICENCE = "Licence Ouverte v2.0 (Etalab)"
PATERNITE = "Direction de l'information légale et administrative (DILA)"
PRODUCTEUR = "Direction Générale du Travail, ministère du Travail"


def archive_date(name_or_url: str) -> Optional[str]:
    """`ACCO_20260601-140000.tar.gz` → `2026-06-01`. None si le nom ne la porte pas."""
    m = _ARCHIVE_DATE_RE.search(name_or_url)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "france-opendata/acco-ingest (+https://github.com/otomata-tech/france-opendata)"
    return s


def list_weekly_archives(sess: Optional[requests.Session] = None, since: Optional[str] = None) -> list[str]:
    """URLs des archives hebdo en ligne (triées), filtrées par date >= `since` (YYYY-MM-DD)."""
    sess = sess or _session()
    resp = sess.get(f"{BASE_URL}/", timeout=60)
    resp.raise_for_status()
    names = sorted(set(_ARCHIVE_RE.findall(resp.text)))
    since_compact = since.replace("-", "") if since else None
    out = []
    for n in names:
        day = n.split("_")[1][:8]  # ACCO_YYYYMMDD-...
        if since_compact and day < since_compact:
            continue
        out.append(f"{BASE_URL}/{n}")
    return out


def _rows_from_tar_stream(fileobj, source: Optional[str] = None,
                          limit: Optional[int] = None) -> Iterator[dict[str, Any]]:
    """Itère les accords d'un flux tar.gz (membres XML métadonnées uniquement).

    `source` = l'URL longue (ou le chemin) d'où vient l'archive : reportée sur chaque
    ligne, parce que la licence de rediffusion l'exige et qu'elle n'est plus
    reconstituable une fois la ligne en base.
    """
    n = 0
    provenance = {"source_archive_url": source, "source_archive_date": archive_date(source or "")}
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:  # r|gz = streaming, mono-passe
        for member in tar:
            if not member.isfile() or not _MEMBER_RE.search(member.name):
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            row = parse_acco(f.read())
            if row is not None:
                yield {**row, **provenance}
                n += 1
                if limit and n >= limit:
                    return


def rows_from_archive(url_or_path: str, sess: Optional[requests.Session] = None,
                      limit: Optional[int] = None) -> Iterator[dict[str, Any]]:
    """Itère les accords d'une archive tar.gz, locale (chemin) ou distante (URL, streamée)."""
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        sess = sess or _session()
        with sess.get(url_or_path, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            resp.raw.decode_content = True
            yield from _rows_from_tar_stream(resp.raw, source=url_or_path, limit=limit)
    else:
        # Archive locale : on ne peut pas inventer l'URL longue dont elle vient. La
        # provenance reste vide plutôt que reconstruite — l'ingestion nominale passe
        # par l'URL, et une ligne sans provenance se signale au lieu de mentir.
        with open(url_or_path, "rb") as fh:
            yield from _rows_from_tar_stream(fh, source=None, limit=limit)
