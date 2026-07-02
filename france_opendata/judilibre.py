"""Judilibre — jurisprudence judiciaire live (Cour de cassation, via PISTE).

~6,5M décisions (Cass + cours d'appel + tribunaux judiciaires), bien au-delà des
bulks DILA. **Auth OAuth2 PISTE requise** (client_credentials) : créer une
application sur piste.gouv.fr, souscrire à l'API Judilibre, fournir
`PISTE_CLIENT_ID` / `PISTE_CLIENT_SECRET` (env ou constructeur).

Endpoints utilisés :
  - `/export` : dump paginé par lots (bootstrap, filtrable par dates/juridiction) ;
  - `/transactionalhistory` : flux incrémental create/update/delete (maintenance) ;
  - `/decision?id=` : texte intégral d'une décision.

`iter_export` / `iter_history` produisent des dicts au schéma `juri_decisions`
(france-opendata-service#9) : id = id hex Judilibre. Les deletes de l'history
sont produits comme `{"id": …, "_deleted": True}` — au consommateur de purger.

Adapté de `judilibre_sync.py` de justicelibre (MIT) — avec NOS credentials.
"""
from __future__ import annotations

import os
import time
from typing import Any, Iterator, Optional

import requests

OAUTH_URL = "https://oauth.piste.gouv.fr/api/oauth/token"
BASE_URL = "https://api.piste.gouv.fr/cassation/judilibre/v1.0"

_JURI_LABEL = {"cc": "Cour de cassation", "ca": "Cour d'appel", "tj": "Tribunal judiciaire"}


class JudilibreClient:
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.client_id = client_id or os.environ.get("PISTE_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("PISTE_CLIENT_SECRET")
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "credentials PISTE absents (PISTE_CLIENT_ID / PISTE_CLIENT_SECRET) — "
                "créer une app sur piste.gouv.fr et souscrire à l'API Judilibre")
        self._sess = requests.Session()
        self._sess.headers["User-Agent"] = "france-opendata/judilibre (+https://github.com/otomata-tech/france-opendata)"
        self._token_at = 0.0

    def _auth(self) -> None:
        if time.time() - self._token_at < 3000:  # tokens PISTE ~1h
            return
        r = requests.post(OAUTH_URL, data={
            "grant_type": "client_credentials", "client_id": self.client_id,
            "client_secret": self.client_secret, "scope": "openid"}, timeout=20)
        r.raise_for_status()
        self._sess.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        self._token_at = time.time()

    def _get(self, path: str, **params) -> dict:
        self._auth()
        for attempt in range(2):
            try:
                r = self._sess.get(f"{BASE_URL}{path}", params=params, timeout=60)
                r.raise_for_status()
                return r.json()
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise
        raise RuntimeError("unreachable")

    def decision(self, decision_id: str) -> Optional[dict]:
        try:
            return self._get("/decision", id=decision_id)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def iter_export(self, *, jurisdiction: str = "cc", date_start: Optional[str] = None,
                    date_end: Optional[str] = None, batch_size: int = 1000) -> Iterator[dict[str, Any]]:
        """Dump paginé d'une juridiction (cc | ca | tj), fenêtrable par dates."""
        batch = 0
        while True:
            params: dict[str, Any] = {"jurisdiction": jurisdiction, "batch": batch,
                                      "batch_size": batch_size, "resolve_references": "false"}
            if date_start:
                params["date_start"] = date_start
            if date_end:
                params["date_end"] = date_end
            data = self._get("/export", **params)
            results = data.get("results") or []
            if not results:
                return
            for d in results:
                yield _map(d)
            if not data.get("next_batch"):
                return
            batch += 1

    def iter_history(self, since_iso: str) -> Iterator[dict[str, Any]]:
        """Flux incrémental depuis `since_iso` (deletes = {"id", "_deleted": True})."""
        params: dict[str, Any] = {"date": since_iso}
        while True:
            data = self._get("/transactionalhistory", **params)
            for tx in data.get("transactions") or []:
                did = tx.get("id")
                if not did:
                    continue
                if (tx.get("operation") or tx.get("type")) == "delete":
                    yield {"id": did, "_deleted": True}
                    continue
                d = self.decision(did)
                if d:
                    yield _map(d)
            next_page = data.get("next_page")
            if not next_page:
                return
            from urllib.parse import parse_qs
            params = {k: v[0] for k, v in parse_qs(next_page.lstrip("?")).items()}


def _map(d: dict) -> dict[str, Any]:
    juri = (d.get("jurisdiction") or "").lower()
    juridiction = _JURI_LABEL.get(juri, d.get("jurisdiction") or "")
    location = d.get("location")
    if juri in ("ca", "tj") and location:
        juridiction = f"{juridiction} {location}" if location else juridiction
    return {
        "id": d["id"],
        "titre": (d.get("titlesAndSummaries") or {}).get("title") or d.get("summary") or None,
        "juridiction": juridiction or None,
        "numero": d.get("number") or None,
        "date_dec": (d.get("decision_date") or "")[:10] or None,
        "solution": d.get("solution") or None,
        "formation": d.get("chamber") or d.get("formation") or None,
        "ecli": d.get("ecli") or None,
        "texte": d.get("text") or "",
    }
