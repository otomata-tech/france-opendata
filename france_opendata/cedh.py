"""CEDH — Cour européenne des droits de l'homme (HUDOC, API publique sans auth).

~76k documents en français. Deux endpoints :
  - liste : `/app/query/results` (pagination start/length, **sort obligatoire**
    sinon 404, cap serveur ~10k par requête → partition par année) ;
  - texte : `/app/conversion/docx/html/body?library=ECHR&id=<itemid>` (HTML → brut).

`iter_decisions(year)` produit des dicts au schéma `juri_decisions` du consommateur
(france-opendata-service#9) : id = itemid HUDOC (`001-XXXXXX`). Crawl **année par
année, du plus récent au plus ancien** : reprenable et incrémental par nature.

Adapté de `scrape_cedh.py` de justicelibre (MIT).
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Iterator, Optional

import requests

BASE = "https://hudoc.echr.coe.int"
BATCH = 100
FIRST_YEAR = 1959  # création de la Cour

_QUERY_BASE = (
    'contentsitename=ECHR AND '
    '(NOT (doctype=PR OR doctype=HFCOMOLD OR doctype=HECOMOLD)) AND '
    '((languageisocode="FRE"))'
)
_SELECT = "itemid,docname,ecli,kpdate,doctype,article,conclusion,importance,respondent,originatingbody_name"

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "france-opendata/cedh (+https://github.com/otomata-tech/france-opendata)"
    return s


def html_to_text(raw: str) -> str:
    """HTML → texte brut (les conversions HUDOC/EUR-Lex, pas le XML DILA)."""
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _list_batch(sess: requests.Session, query: str, start: int) -> dict:
    from urllib.parse import quote
    url = (f"{BASE}/app/query/results?query={quote(query, safe='')}"
           f"&select={quote(_SELECT, safe='')}"
           f"&sort={quote('kpdate Descending', safe='')}&start={start}&length={BATCH}")
    r = sess.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_text(itemid: str, sess: Optional[requests.Session] = None) -> str:
    sess = sess or _session()
    r = sess.get(f"{BASE}/app/conversion/docx/html/body",
                 params={"library": "ECHR", "id": itemid, "filename": "x.docx",
                         "logEvent": "False"},
                 timeout=60)
    if r.status_code != 200:
        return ""
    return html_to_text(r.text)


def iter_decisions(year: int, sess: Optional[requests.Session] = None,
                   with_text: bool = True) -> Iterator[dict[str, Any]]:
    """Itère les documents FR d'une année (les métadonnées, + texte si demandé)."""
    sess = sess or _session()
    query = (f"{_QUERY_BASE} AND kpdate:[{year}-01-01T00:00:00.0Z "
             f"TO {year}-12-31T23:59:59.0Z]")
    start = 0
    while True:
        data = _list_batch(sess, query, start)
        results = data.get("results", [])
        if not results:
            return
        for item in results:
            c = item.get("columns", {})
            itemid = c.get("itemid")
            if not itemid:
                continue
            yield {
                "id": itemid,
                "titre": c.get("docname") or None,
                "juridiction": "Cour européenne des droits de l'homme",
                "numero": c.get("respondent") or None,
                "date_dec": (c.get("kpdate") or "")[:10] or None,
                "solution": (c.get("conclusion") or "")[:500] or None,
                "formation": c.get("originatingbody_name") or None,
                "ecli": c.get("ecli") or None,
                "texte": fetch_text(itemid, sess) if with_text else "",
            }
        start += BATCH
        if start >= data.get("resultcount", 0):
            return
