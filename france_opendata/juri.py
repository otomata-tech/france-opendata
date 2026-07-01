"""JURI — jurisprudence française (fonds DILA) : parseur générique de décisions.

Six fonds DILA partagent le même modèle de diffusion (global + quotidiens) et,
à l'exception de CNIL, le même bloc `META_JURI` :

  - `CASS`    : arrêts publiés de la Cour de cassation (JURITEXT…)
  - `INCA`    : arrêts inédits de la Cour de cassation (JURITEXT…)
  - `CAPP`    : cours d'appel (JURITEXT…)
  - `JADE`    : juridictions administratives — CE, CAA, TA (CETATEXT…)
  - `CONSTIT` : Conseil constitutionnel (CONSTEXT…)
  - `CNIL`    : délibérations CNIL (CNILTEXT…) — bloc `META_CNIL` spécifique

`parse_juri_decision(xml_bytes)` normalise les deux variantes vers un dict plat
(colonnes `DECISION_COLUMNS`) : identité (juridiction, numéro, date, solution),
contexte (formation, ECLI) et texte intégral. Le crawl vit dans `juri_ingest`,
le stockage/tri par autorité au consommateur (france-opendata-service#8).

Le parsing est durci defusedxml (extra `france-opendata[stock]`).
"""
from __future__ import annotations

from typing import Any, Optional

from .kali import _date, _safe_root, _txt, strip_html

# Colonnes plates produites par le parseur (ordre stable, partagé avec l'ingestion).
DECISION_COLUMNS = [
    "id", "titre", "juridiction", "numero", "date_dec", "solution",
    "formation", "ecli", "texte",
]


def parse_juri_decision(xml_bytes: bytes) -> Optional[dict[str, Any]]:
    """Parse une décision (bytes XML) → dict de colonnes, ou None si illisible.

    Deux variantes : `META_JURI` (CASS/INCA/CAPP/JADE/CONSTIT — la FORMATION et
    l'ECLI vivent dans le bloc fond-spécifique frère) et `META_CNIL` (mapping :
    DATE_TEXTE→date_dec, NATURE_DELIB→solution, juridiction fixée à « CNIL »)."""
    root = _safe_root(xml_bytes)
    if root is None:
        return None
    commun = root.find(".//META_COMMUN")
    _id = _txt(commun, "ID")
    if not _id:
        return None

    def _bloc_texte() -> str:
        elt = root.find(".//BLOC_TEXTUEL/CONTENU")
        if elt is None:
            elt = root.find(".//CONTENU")
        if elt is None:
            return ""
        import xml.etree.ElementTree as ET  # sérialisation seule (arbre déjà défusé)
        return strip_html(ET.tostring(elt, encoding="unicode"))

    meta_juri = root.find(".//META_JURI")
    if meta_juri is not None:
        # FORMATION / ECLI : dans META_JURI_JUDI, META_JURI_ADMIN ou META_JURI_CONSTIT.
        formation = ecli = None
        meta_spec = root.find(".//META_SPEC")
        for child in (meta_spec if meta_spec is not None else []):
            if child.tag == "META_JURI":
                continue
            formation = formation or _txt(child, "FORMATION")
            ecli = ecli or _txt(child, "ECLI")
        return {
            "id": _id,
            "titre": _txt(meta_juri, "TITRE"),
            "juridiction": _txt(meta_juri, "JURIDICTION"),
            "numero": _txt(meta_juri, "NUMERO"),
            "date_dec": _date(_txt(meta_juri, "DATE_DEC")),
            "solution": _txt(meta_juri, "SOLUTION"),
            "formation": formation,
            "ecli": ecli,
            "texte": _bloc_texte(),
        }

    meta_cnil = root.find(".//META_CNIL")
    if meta_cnil is not None:
        return {
            "id": _id,
            "titre": _txt(meta_cnil, "TITREFULL") or _txt(meta_cnil, "TITRE"),
            "juridiction": "CNIL",
            "numero": _txt(meta_cnil, "NUMERO"),
            "date_dec": _date(_txt(meta_cnil, "DATE_TEXTE")),
            "solution": _txt(meta_cnil, "NATURE_DELIB"),
            "formation": None,
            "ecli": None,
            "texte": _bloc_texte(),
        }

    return None
