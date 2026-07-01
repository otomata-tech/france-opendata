"""KALI — conventions collectives nationales et accords de branche (DILA).

Base des conventions collectives (CCN), accords de branche, avenants et arrêtés
d'extension, identifiés par leur IDCC (numéro à 4 chiffres). Consultable sur
Légifrance ; diffusée en open data en dump XML DILA
(`echanges.dila.gouv.fr/OPENDATA/KALI/`), quatre types d'objets :

  - `KALICONT` (conteneur)  : LA convention au sens IDCC — porte `META_CONTENEUR/NUM`
    (= IDCC), titre, état ; sa `STRUCTURE_TXT` liste les textes rattachés.
  - `KALITEXT` (texte)      : texte de base, texte attaché (avenant, accord), texte
    salaires ou arrêté d'extension.
  - `KALIARTI` (article)    : l'article — porte son propre rattachement via
    `CONTEXTE/TEXTE@cid` (texte parent + titre) et `CONTEXTE/CONTENEUR@cid`
    (conteneur → IDCC par jointure).
  - `KALISCTA` (section_ta) : table des matières de section (non parsée ici — le
    titre du texte parent suffit à contextualiser un article ; cf. issue #6).

Ce module est le **parseur de format** : `parse_kali_conteneur(xml_bytes)` et
`parse_kali_article(xml_bytes)` → dicts plats (colonnes `CONTENEUR_COLUMNS` /
`ARTICLE_COLUMNS`). Le crawl du dump vit dans `kali_ingest`. Le **stockage, la
jointure article→IDCC et la recherche** sont au consommateur —
france-opendata-service les indexe en PostgreSQL (FTS `french` + filtre IDCC).

⚠️ Le piège du format (celui qui casse le filtre IDCC de justicelibre) : l'IDCC ne
figure QUE sur le conteneur (`META_CONTENEUR/NUM`), jamais sur l'article. Un article
ne se comprend qu'en résolvant `CONTEXTE/CONTENEUR@cid` → conteneur.

Le parsing est durci defusedxml (extra `france-opendata[stock]`).
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Colonnes plates produites par les parseurs (ordre stable, partagé avec l'ingestion).
CONTENEUR_COLUMNS = ["id", "idcc", "titre", "etat", "date_publi"]
ARTICLE_COLUMNS = [
    "id", "conteneur_id", "texte_id", "texte_titre", "texte_nature",
    "date_signature", "num", "etat", "date_debut", "date_fin", "texte",
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")

# Balises bloc du HTML DILA → saut de ligne, pour un texte lisible après strip.
_BLOCK_RE = re.compile(r"</?(?:p|br|div|tr|table|li|ul|ol|blockquote|h[1-6])[^>]*>", re.I)


def strip_html(html: str) -> str:
    """Aplati le HTML DILA (BLOC_TEXTUEL/CONTENU) en texte brut, lignes préservées."""
    text = _BLOCK_RE.sub("\n", html)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&#13;", "\n")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _NL_RE.sub("\n\n", text).strip()


def _txt(elem, name: str) -> Optional[str]:
    if elem is None:
        return None
    node = elem.find(name)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def _date(value: Optional[str]) -> Optional[str]:
    """Dates DILA : '2999-01-01' = « sans fin » → None (plus simple à requêter)."""
    if not value or value.startswith("2999"):
        return None
    return value


def _safe_root(xml_bytes: bytes):
    from defusedxml.ElementTree import fromstring as _safe_fromstring  # extra [stock]
    try:
        return _safe_fromstring(xml_bytes)
    except Exception:  # noqa: BLE001 — ParseError, EntitiesForbidden… → ignoré
        return None


def parse_kali_conteneur(xml_bytes: bytes) -> Optional[dict[str, Any]]:
    """Parse un conteneur KALICONT (bytes XML) → dict de colonnes, ou None si illisible.

    `idcc` = `META_CONTENEUR/NUM` — peut être vide sur les conteneurs non rattachés
    à un IDCC (accords professionnels hors branche) : la ligne reste utile (titre)."""
    root = _safe_root(xml_bytes)
    if root is None:
        return None
    commun = root.find(".//META_COMMUN")
    cont = root.find(".//META_CONTENEUR")
    _id = _txt(commun, "ID")
    if not _id or cont is None:
        return None
    return {
        "id": _id,
        "idcc": _txt(cont, "NUM"),
        "titre": _txt(cont, "TITRE"),
        "etat": _txt(cont, "ETAT"),
        "date_publi": _date(_txt(cont, "DATE_PUBLI")),
    }


def parse_kali_article(xml_bytes: bytes) -> Optional[dict[str, Any]]:
    """Parse un article KALIARTI (bytes XML) → dict de colonnes, ou None si illisible.

    Le rattachement est lu dans `CONTEXTE` : `TEXTE@cid` (texte parent, avec
    `TITRE_TXT` = intitulé de l'avenant/accord) et `CONTENEUR@cid` (convention).
    L'IDCC n'est PAS ici — jointure `conteneur_id` → conteneur au stockage."""
    root = _safe_root(xml_bytes)
    if root is None:
        return None
    commun = root.find(".//META_COMMUN")
    _id = _txt(commun, "ID")
    if not _id:
        return None

    contexte_texte = root.find(".//CONTEXTE/TEXTE")
    conteneur = root.find(".//CONTEXTE/CONTENEUR")  # frère de TEXTE, pas enfant
    titre_txt = root.find(".//CONTEXTE/TEXTE/TITRE_TXT")
    meta_article = root.find(".//META_ARTICLE")

    contenu = root.find(".//BLOC_TEXTUEL/CONTENU")
    if contenu is not None:
        import xml.etree.ElementTree as ET  # sérialisation seule (arbre déjà défusé)
        texte = strip_html(ET.tostring(contenu, encoding="unicode"))
    else:
        texte = ""

    titre_court = titre_txt.get("c_titre_court") if titre_txt is not None else None
    titre_long = titre_txt.text.strip() if titre_txt is not None and titre_txt.text else None

    return {
        "id": _id,
        "conteneur_id": conteneur.get("cid") if conteneur is not None else None,
        "texte_id": contexte_texte.get("cid") if contexte_texte is not None else None,
        "texte_titre": titre_long or titre_court,
        "texte_nature": contexte_texte.get("nature") if contexte_texte is not None else None,
        "date_signature": _date(contexte_texte.get("date_signature") if contexte_texte is not None else None),
        "num": _txt(meta_article, "NUM"),
        "etat": _txt(meta_article, "ETAT"),
        "date_debut": _date(_txt(meta_article, "DATE_DEBUT")),
        "date_fin": _date(_txt(meta_article, "DATE_FIN")),
        "texte": texte,
    }
