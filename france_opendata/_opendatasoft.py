"""Helpers Opendatasoft Explore v2.1 — mutualisés par les clients qui tapent une
plateforme ODS (Enedis, culture/spectacle, etc.).

Deux modes :
- `ods_records(...)` : endpoint `/records`, paginé (limit/offset). Plafond dur à
  offset=10000 côté ODS → réservé aux requêtes qui tiennent sous 10k lignes.
- `ods_export(...)` : endpoint `/exports/json`, renvoie TOUT le filtre `where` en
  une réponse (pas de plafond d'offset). À privilégier pour les grosses partitions.

Pas de logique métier ici : juste l'accès HTTP + la pagination.
"""
from __future__ import annotations

from typing import Any, Optional

import requests


def ods_records(
    records_url: str,
    *,
    where: Optional[str] = None,
    select: Optional[str] = None,
    order_by: Optional[str] = None,
    limit: int = 100,
    max_records: int = 1000,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """Itère les records ODS Explore v2.1 (`/records`) jusqu'à `max_records`.

    `records_url` pointe sur `.../datasets/<dataset>/records`.
    """
    get = (session or requests).get
    out: list[dict[str, Any]] = []
    offset = 0
    while offset <= max_records:
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if where:
            params["where"] = where
        if select:
            params["select"] = select
        if order_by:
            params["order_by"] = order_by
        resp = get(records_url, params=params, timeout=timeout)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        out.extend(results)
        if len(results) < limit:
            break
        offset += limit
    return out


def ods_export(
    records_url: str,
    *,
    where: Optional[str] = None,
    limit: int = -1,
    session: Optional[requests.Session] = None,
    timeout: int = 120,
) -> list[dict[str, Any]]:
    """Export complet (`/exports/json`) d'un filtre `where`, sans plafond d'offset.

    `records_url` est l'URL `/records` du dataset : on dérive `/exports/json`.
    `limit=-1` = tout ; une valeur positive borne (debug).
    """
    export_url = records_url.rsplit("/records", 1)[0] + "/exports/json"
    params = {"limit": str(limit)}
    if where:
        params["where"] = where
    resp = (session or requests).get(export_url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    # /exports/json renvoie soit un array, soit {"results": [...]} selon la version.
    if isinstance(data, dict):
        return data.get("results", [])
    return data
