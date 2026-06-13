"""Client générique Opendatasoft Explore v2.1 (open data, sans clé).

Cible n'importe quel portail ODS public : data.culture.gouv.fr,
data.economie.gouv.fr, opendata.enedis.fr, ANCT, ADEME, portails régionaux…

Deux modes de lecture :
- `records(...)` : endpoint `/records`, paginé (limit/offset, plafond ODS à
  offset=10000), avec `select`/`order_by`/`group_by`/`refine`.
- `export(...)` : endpoint `/exports/<fmt>`, renvoie TOUT le filtre `where` en une
  réponse (pas de plafond d'offset) — à privilégier pour les grosses partitions.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional
from urllib.parse import urlencode

import requests


class OpendatasoftClient:
    """Wrapper de l'API Opendatasoft Explore v2.1.

    Args:
        base_url: racine du portail, ex. "https://data.culture.gouv.fr".
        timeout: timeout HTTP en secondes.
    """

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _records_url(self, dataset_id: str) -> str:
        return f"{self.base_url}/api/explore/v2.1/catalog/datasets/{dataset_id}/records"

    def _exports_url(self, dataset_id: str, fmt: str) -> str:
        return f"{self.base_url}/api/explore/v2.1/catalog/datasets/{dataset_id}/exports/{fmt}"

    def _facets_url(self, dataset_id: str) -> str:
        return f"{self.base_url}/api/explore/v2.1/catalog/datasets/{dataset_id}/facets"

    def records(
        self,
        dataset_id: str,
        *,
        where: Optional[str] = None,
        select: Optional[str] = None,
        order_by: Optional[str] = None,
        group_by: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        refine: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        """Interroge `/records`. Renvoie le JSON brut (`{"total_count", "results"}`)."""
        params: list[tuple[str, str]] = []
        if where: params.append(("where", where))
        if select: params.append(("select", select))
        if order_by: params.append(("order_by", order_by))
        if group_by: params.append(("group_by", group_by))
        params.append(("limit", str(min(100, max(1, limit)))))
        params.append(("offset", str(max(0, offset))))
        if refine:
            for k, v in refine.items():
                params.append(("refine", f"{k}:{v}"))
        resp = requests.get(self._records_url(dataset_id), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def iter_records(
        self,
        dataset_id: str,
        *,
        where: Optional[str] = None,
        select: Optional[str] = None,
        order_by: Optional[str] = None,
        page_size: int = 100,
        max_total: Optional[int] = None,
    ):
        """Pagine `/records` jusqu'à épuisement ou `max_total`. Yield des dicts."""
        offset = 0
        yielded = 0
        while True:
            page = self.records(
                dataset_id,
                where=where, select=select, order_by=order_by,
                limit=page_size, offset=offset,
            )
            results = page.get("results", [])
            if not results:
                return
            for row in results:
                yield row
                yielded += 1
                if max_total is not None and yielded >= max_total:
                    return
            if len(results) < page_size:
                return
            offset += page_size

    def export(
        self,
        dataset_id: str,
        fmt: str = "json",
        *,
        where: Optional[str] = None,
        limit: int = -1,
    ) -> Any:
        """Export complet (`/exports/<fmt>`), sans plafond d'offset.

        `fmt="json"` → liste de dicts ; sinon → texte brut (csv…). `limit=-1` = tout.
        """
        params: dict[str, str] = {"limit": str(limit)}
        if where:
            params["where"] = where
        resp = requests.get(self._exports_url(dataset_id, fmt), params=params, timeout=self.timeout)
        resp.raise_for_status()
        if fmt == "json":
            data = resp.json()
            return data.get("results", []) if isinstance(data, dict) else data
        return resp.text

    def export_url(self, dataset_id: str, fmt: str = "csv", *, where: Optional[str] = None) -> str:
        """URL d'export directe — l'appelant la streame (potentiellement volumineux)."""
        q = {}
        if where:
            q["where"] = where
        qs = ("?" + urlencode(q)) if q else ""
        return f"{self._exports_url(dataset_id, fmt)}{qs}"

    def facets(
        self,
        dataset_id: str,
        facets: list[str],
        *,
        where: Optional[str] = None,
    ) -> dict[str, Any]:
        """Comptes par facette. `facets` = liste de noms de champs."""
        params: list[tuple[str, str]] = [("facet", f) for f in facets]
        if where:
            params.append(("where", where))
        resp = requests.get(self._facets_url(dataset_id), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
