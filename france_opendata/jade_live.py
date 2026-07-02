"""JADE live — opendata.justice-administrative.fr (API publique, sans auth).

Le bulk JADE DILA s'arrête au dump global (+ deltas) ; le portail open data du
Conseil d'État expose ~1M décisions dont les récentes absentes du bulk. Deux
endpoints (relevés du frontend par justicelibre) :
  - recherche fenêtrée par dates : `/model_search_date_juri/openData/Date_Lecture/
    {q}/{juri}/{d_start}/{d_end}/{limit}` (q="*" = tout) ;
  - texte intégral : `/elastic/decisions/{id}/<noSecond>` (champ `paragraph`,
    séparateur `$$$`).

`iter_decisions(juri, date_start, date_end)` produit des dicts au schéma
`juri_decisions` (fond `jade` — ids `CETATEXT…` identiques au bulk DILA, les
doublons se règlent par upsert). Fenêtrer par mois glissant pour l'incrémental.

Adapté de `download_opendata.py` de justicelibre (MIT).
"""
from __future__ import annotations

from typing import Any, Iterator, Optional
from urllib.parse import quote

import requests

API_BASE = "https://opendata.justice-administrative.fr/recherche/api"
_NO_SECOND = "bm9TZWNvbmR2YWx1ZQ=="
LIMIT_PER_CALL = 10_000

# Codes juridiction du portail : CE + 9 CAA + 40 TA.
JURIDICTIONS = [
    "CE",
    "CAA_BORDEAUX", "CAA_DOUAI", "CAA_LYON", "CAA_MARSEILLE", "CAA_NANCY",
    "CAA_NANTES", "CAA_PARIS", "CAA_TOULOUSE", "CAA_VERSAILLES",
    "TA06", "TA13", "TA14", "TA20", "TA21", "TA25", "TA30", "TA31",
    "TA33", "TA34", "TA35", "TA38", "TA44", "TA45", "TA51", "TA54",
    "TA59", "TA63", "TA64", "TA67", "TA69", "TA75", "TA76", "TA77",
    "TA78", "TA80", "TA83", "TA86", "TA87", "TA93", "TA95",
    "TA101", "TA102", "TA103", "TA104", "TA105", "TA106", "TA107",
    "TA108", "TA109",
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "france-opendata/jade-live (+https://github.com/otomata-tech/france-opendata)"
    return s


def fetch_text(decision_id: str, sess: Optional[requests.Session] = None) -> str:
    sess = sess or _session()
    r = sess.get(f"{API_BASE}/elastic/decisions/{decision_id}/{_NO_SECOND}", timeout=60)
    if r.status_code != 200:
        return ""
    # Réponse enveloppée comme la recherche : decisions.body.hits.hits[0]._source.
    hits = r.json().get("decisions", {}).get("body", {}).get("hits", {}).get("hits", [])
    src = hits[0].get("_source", {}) if hits else {}
    return (src.get("paragraph") or "").replace("$$$", "\n\n")


def iter_decisions(juri: str, date_start: str, date_end: str,
                   sess: Optional[requests.Session] = None,
                   with_text: bool = True) -> Iterator[dict[str, Any]]:
    """Itère les décisions d'une juridiction sur [date_start, date_end] (YYYY-MM-DD).

    ⚠️ Cap serveur à 10k par fenêtre — resserrer la fenêtre si `total >= 10k`."""
    sess = sess or _session()
    url = (f"{API_BASE}/model_search_date_juri/openData/Date_Lecture/"
           f"{quote('*', safe='')}/{juri}/{date_start}/{date_end}/{LIMIT_PER_CALL}")
    r = sess.get(url, timeout=120)
    r.raise_for_status()
    hits = r.json().get("decisions", {}).get("body", {}).get("hits", {})
    for hit in hits.get("hits", []):
        src = hit.get("_source", {})
        _id = hit.get("_id")
        if not _id:
            continue
        yield {
            "id": _id,
            "titre": src.get("Titre") or None,
            "juridiction": src.get("Nom_Juridiction") or juri,
            "numero": src.get("Numero_Dossier") or None,
            "date_dec": (src.get("Date_Lecture") or "")[:10] or None,
            "solution": src.get("Type_Decision") or None,
            "formation": src.get("Formation_Jugement") or None,
            "ecli": src.get("Numero_ECLI") or None,
            "texte": fetch_text(_id, sess) if with_text else "",
        }
