"""ACCO — accords d'entreprise (base nationale des accords collectifs, DILA).

Base publique des accords d'entreprise **conclus depuis le 1er septembre 2017**
(décret 2017-752 sur la publicité des accords collectifs), déposés via TéléAccords.
Consultable sur Légifrance ; diffusée en open data sous forme de dump XML DILA
(`echanges.dila.gouv.fr/OPENDATA/ACCO/`) : un `<TEXTE_ACCO>` XML de métadonnées par
accord.

Ce module est le **parseur de format** : `parse_acco(xml_bytes)` → dict plat (colonnes
`COLUMNS`). Le crawl du dump DILA vit dans `acco_ingest`. Le **stockage et la
recherche** sont laissés au consommateur — oto-backend les indexe en PostgreSQL
(table `acco`, ~387k lignes ; le parquet/DuckDB est réservé au monstre SIRENE). Pas
de client de requête ici : un seul moteur de recherche, côté backend.

Un « accord » porte une NATURE (ACCORD initial, AVENANT = renégociation, ...) et des
THÈMES codés. Thèmes protection sociale (cf. `THEME_PREVOYANCE`) :
    111 = Couverture complémentaire santé - maladie
    112 = Prévoyance collective, autre que santé maladie
    113 = Retraite complémentaire - supplémentaire

⚠️ `conforme_version_integrale=false` (fréquent) : seules les métadonnées sont
publiées, pas le texte intégral des clauses. Suffit à *détecter* qui a négocié quoi
et quand, pas toujours à lire le détail du contrat.

Le parsing est durci defusedxml (extra `france-opendata[stock]`).
"""
from __future__ import annotations

import json
from typing import Any, Optional

# Codes thèmes « protection sociale complémentaire » (santé / prévoyance / retraite suppl.).
THEME_PREVOYANCE = ("111", "112", "113")

# Colonnes plates produites par parse_acco (ordre stable, partagé avec l'ingestion).
COLUMNS = [
    "id", "nature", "numero", "siret", "raison_sociale", "code_ape", "code_idcc",
    "secteur", "date_texte", "date_depot", "date_effet", "date_fin", "date_maj",
    "date_diffusion", "conforme_version_integrale", "theme_codes", "themes_libelle",
    "syndicats_libelle", "code_postal", "ville", "titre", "url",
]


# ---------------------------------------------------------------------------
# Parsing d'un accord (XML DILA <TEXTE_ACCO>) → dict plat de colonnes.
# Durci defusedxml (XXE / billion-laughs) : déposé par des tiers = non fiable.
# ---------------------------------------------------------------------------

def _txt(elem, name: str) -> Optional[str]:
    if elem is None:
        return None
    node = elem.find(name)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def parse_acco(xml_bytes: bytes) -> Optional[dict[str, Any]]:
    """Parse un accord ACCO (bytes XML) → dict de colonnes, ou None si illisible."""
    from defusedxml.ElementTree import fromstring as _safe_fromstring  # extra [stock]
    try:
        root = _safe_fromstring(xml_bytes)
    except Exception:  # noqa: BLE001 — ParseError, EntitiesForbidden… → ignoré
        return None

    commun = root.find(".//META_COMMUN")
    acco = root.find(".//META_ACCO")
    if acco is None:
        return None
    _id = _txt(commun, "ID")
    if not _id:
        return None

    theme_codes = [c.text.strip() for c in acco.findall(".//THEME/CODE") if c.text and c.text.strip()]
    theme_libs = [l.text.strip() for l in acco.findall(".//THEME/LIBELLE") if l.text and l.text.strip()]
    syndicats = [l.text.strip() for l in acco.findall(".//SYNDICAT/LIBELLE") if l.text and l.text.strip()]
    adresse = acco.find(".//ADRESSE_POSTALE")

    return {
        "id": _id,
        "nature": _txt(commun, "NATURE"),
        "numero": _txt(acco, "NUMERO"),
        "siret": _txt(acco, "SIRET"),
        "raison_sociale": _txt(acco, "RAISON_SOCIALE"),
        "code_ape": _txt(acco, "CODE_APE"),
        "code_idcc": _txt(acco, "CODE_IDCC"),
        "secteur": _txt(acco, "SECTEUR"),
        "date_texte": _txt(acco, "DATE_TEXTE"),
        "date_depot": _txt(acco, "DATE_DEPOT"),
        "date_effet": _txt(acco, "DATE_EFFET"),
        "date_fin": _txt(acco, "DATE_FIN"),
        "date_maj": _txt(acco, "DATE_MAJ"),
        "date_diffusion": _txt(acco, "DATE_DIFFUSION"),
        "conforme_version_integrale": _txt(acco, "CONFORME_VERSION_INTEGRALE"),
        "theme_codes": json.dumps(theme_codes) if theme_codes else None,
        "themes_libelle": " | ".join(theme_libs) or None,
        "syndicats_libelle": " | ".join(syndicats) or None,
        "code_postal": _txt(adresse, "CODE_POSTAL"),
        "ville": _txt(adresse, "VILLE"),
        "titre": _txt(acco, "TITRE_TXT"),
        "url": _txt(commun, "URL"),
    }
