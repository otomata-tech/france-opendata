"""CJUE — Cour de justice de l'UE (EUR-Lex / publications.europa.eu, sans auth).

~44k arrêts, ordonnances et conclusions d'AG en français. Deux endpoints :
  - liste : SPARQL `publications.europa.eu/webapi/rdf/sparql` (CELEX secteur 6,
    fenêtré par dates — pagination LIMIT/OFFSET) ;
  - texte : `publications.europa.eu/resource/celex/<CELEX>` en `Accept-Language: fra`.

`iter_decisions(year)` produit des dicts au schéma `juri_decisions`
(france-opendata-service#9) : id = CELEX (`62023CJ0123`), ECLI dérivé du CELEX.
Crawl année par année, reprenable.

Adapté de `scrape_cjue.py` de justicelibre (MIT).
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Optional

import requests

from .cedh import html_to_text

SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
RESOURCE_BASE = "http://publications.europa.eu/resource/celex"
PAGE = 1000
FIRST_YEAR = 1954  # CECA

_SPARQL_QUERY = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?celex ?date ?type WHERE {
  ?work cdm:work_has_resource-type ?rtype .
  VALUES ?rtype {
    <http://publications.europa.eu/resource/authority/resource-type/JUDG>
    <http://publications.europa.eu/resource/authority/resource-type/JUDG_GNR>
    <http://publications.europa.eu/resource/authority/resource-type/JUDG_JURINFO>
    <http://publications.europa.eu/resource/authority/resource-type/ORDER>
    <http://publications.europa.eu/resource/authority/resource-type/OPIN_AG>
  }
  ?work cdm:resource_legal_id_celex ?celex .
  ?work cdm:work_date_document ?date .
  FILTER(STRSTARTS(STR(?celex), "6"))
  FILTER(?date >= "%(date_start)s"^^xsd:date && ?date < "%(date_end)s"^^xsd:date)
  BIND(?rtype AS ?type)
}
ORDER BY DESC(?date)
LIMIT %(limit)d OFFSET %(offset)d
"""

_TYPE_LABEL = {
    "JUDG": "Arrêt", "JUDG_GNR": "Arrêt", "JUDG_JURINFO": "Arrêt",
    "ORDER": "Ordonnance", "OPIN_AG": "Conclusions de l'avocat général",
}

# Juridiction par lettre de cour de l'ECLI (C = Cour, T = Tribunal, F = TFP).
_COURT_LABEL = {
    "C": "Cour de justice de l'Union européenne",
    "T": "Tribunal de l'Union européenne",
    "F": "Tribunal de la fonction publique de l'Union européenne",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "france-opendata/cjue (+https://github.com/otomata-tech/france-opendata)"
    return s


def celex_to_ecli(celex: str) -> Optional[str]:
    """Mapping CELEX → ECLI (best-effort — certains CELEX n'en ont pas de propre)."""
    m = re.match(r"^6(\d{4})([A-Z]{2})(\d{4})$", celex)
    if not m:
        return None
    year, typ, num = m.groups()
    court = {"CJ": "C", "CO": "C", "CC": "C", "TJ": "T", "TO": "T", "FC": "F"}.get(typ)
    return f"ECLI:EU:{court}:{year}:{int(num)}" if court else None


def fetch_text(celex: str, sess: Optional[requests.Session] = None) -> tuple[str, str]:
    """(titre, texte brut) d'un CELEX, version française."""
    sess = sess or _session()
    r = sess.get(f"{RESOURCE_BASE}/{celex}",
                 headers={"Accept-Language": "fra", "Accept": "text/html"},
                 allow_redirects=True, timeout=60)
    if r.status_code != 200:
        return "", ""
    m = re.search(r"<title>(.*?)</title>", r.text, flags=re.I | re.DOTALL)
    titre = html_to_text(m.group(1)) if m else ""
    return titre, html_to_text(r.text)


def iter_decisions(year: int, sess: Optional[requests.Session] = None,
                   with_text: bool = True) -> Iterator[dict[str, Any]]:
    """Itère les décisions CJUE d'une année (métadonnées SPARQL, + texte si demandé)."""
    sess = sess or _session()
    offset = 0
    while True:
        q = _SPARQL_QUERY % {"date_start": f"{year}-01-01", "date_end": f"{year + 1}-01-01",
                             "limit": PAGE, "offset": offset}
        r = sess.get(SPARQL, params={"query": q, "format": "application/sparql-results+json"},
                     timeout=120)
        r.raise_for_status()
        rows = r.json()["results"]["bindings"]
        if not rows:
            return
        for b in rows:
            celex = b["celex"]["value"]
            rtype = b["type"]["value"].rsplit("/", 1)[-1]
            ecli = celex_to_ecli(celex)
            titre, texte = fetch_text(celex, sess) if with_text else ("", "")
            yield {
                "id": celex,
                "titre": titre or None,
                "juridiction": _COURT_LABEL.get((ecli or "ECLI:EU:C")[8:9], _COURT_LABEL["C"]),
                "numero": celex,
                "date_dec": b["date"]["value"][:10] or None,
                "solution": _TYPE_LABEL.get(rtype) or None,
                "formation": None,
                "ecli": ecli,
                "texte": texte,
            }
        offset += PAGE
