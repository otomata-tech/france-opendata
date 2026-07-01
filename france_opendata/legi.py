"""LEGI — codes et lois consolidés (DILA) : parseur d'articles versionnés.

Base des textes législatifs et réglementaires consolidés, diffusée en dump XML
DILA (`echanges.dila.gouv.fr/OPENDATA/LEGI/`, ~1,2 Go compressé + quotidiens).
L'objet utile ici est l'**article versionné** : un fichier `LEGIARTI` = UNE
version d'un article, bornée par `META_ARTICLE/DATE_DEBUT` / `DATE_FIN`, avec
son état (VIGUEUR, MODIFIE, ABROGE, *_DIFF…). L'ensemble des versions d'un même
article partage le même couple (texte parent, NUM).

C'est ce versionnage qui permet le geste « l'article 1128 du Code civil **au
1992-06-15** » : sélectionner la ligne dont l'intervalle [date_debut, date_fin)
contient la date. Le rattachement au code parent vit dans `CONTEXTE/TEXTE@cid`
(LEGITEXT…) + `TITRE_TXT` (nom du code).

Ce module est le **parseur de format** : `parse_legi_article(xml_bytes)` → dict
plat (colonnes `ARTICLE_COLUMNS`). Le crawl du dump vit dans `legi_ingest`. Le
stockage et la recherche (index par code/num/date, FTS) sont au consommateur —
france-opendata-service (#7).

Le parsing est durci defusedxml (extra `france-opendata[stock]`).
"""
from __future__ import annotations

from typing import Any, Optional

from .kali import _date, _safe_root, _txt, strip_html

# Colonnes plates produites par le parseur (ordre stable, partagé avec l'ingestion).
ARTICLE_COLUMNS = [
    "id", "legitext", "titre_texte", "num", "etat",
    "date_debut", "date_fin", "texte", "nota",
]


def parse_legi_article(xml_bytes: bytes) -> Optional[dict[str, Any]]:
    """Parse une version d'article LEGIARTI (bytes XML) → dict, ou None si illisible.

    `date_fin` : '2999-01-01' DILA (= sans fin) → None. Les états *_DIFF (entrée
    en vigueur différée) sont conservés tels quels — c'est au consommateur de
    filtrer selon le geste (version applicable à une date vs texte à venir)."""
    root = _safe_root(xml_bytes)
    if root is None:
        return None
    commun = root.find(".//META_COMMUN")
    meta_article = root.find(".//META_ARTICLE")
    _id = _txt(commun, "ID")
    if not _id or meta_article is None:
        return None

    contexte = root.find(".//CONTEXTE/TEXTE")
    titre_txt = contexte.find("TITRE_TXT") if contexte is not None else None
    titre = None
    if titre_txt is not None:
        titre = titre_txt.get("c_titre_court") or (titre_txt.text.strip() if titre_txt.text else None)

    def _bloc(path: str) -> str:
        elt = root.find(path)
        if elt is None:
            return ""
        import xml.etree.ElementTree as ET  # sérialisation seule (arbre déjà défusé)
        return strip_html(ET.tostring(elt, encoding="unicode"))

    return {
        "id": _id,
        "legitext": contexte.get("cid") if contexte is not None else None,
        "titre_texte": titre,
        "num": _txt(meta_article, "NUM"),
        "etat": _txt(meta_article, "ETAT"),
        "date_debut": _date(_txt(meta_article, "DATE_DEBUT")),
        "date_fin": _date(_txt(meta_article, "DATE_FIN")),
        "texte": _bloc(".//BLOC_TEXTUEL/CONTENU"),
        "nota": _bloc(".//NOTA/CONTENU") or None,
    }
