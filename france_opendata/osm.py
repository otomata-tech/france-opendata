"""OpenStreetMap — points d'intérêt via l'API Overpass (open data, sans clé).

Recense **tous** les objets OSM portant un tag donné sur une zone, en UN appel
(pas de plafond ni de pagination comme les API Maps commerciales) : l'aire est
une commune / un département (par code INSEE) ou une bounding box.

Usage type : implantation / analyse de site — recenser les parkings, écoles,
équipements, ou commerces d'un type (`shop=laundry`, `amenity=parking`…). ⚠️
Complétude **variable selon le type** : l'infrastructure (parkings, transports,
équipements publics) est très bien cartographiée dans OSM ; les commerces grand
public le sont moins (les enseignes se listent sur Google, pas sur OSM) → pour
un recensement de commerces, croiser avec une source Maps.

Instances publiques Overpass : User-Agent obligatoire (406 sinon) et IP
datacenter throttlées → endpoint de secours + UA explicite.
"""
from __future__ import annotations

from typing import Any, Optional

import requests

ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
TIMEOUT = 90
_UA = "france-opendata/osm (+https://otomata.tech)"


def _selector_clause(selector: str) -> str:
    """'shop=laundry' → '[\"shop\"=\"laundry\"]' ; 'amenity' → '[\"amenity\"]'."""
    s = selector.strip()
    if "=" in s:
        key, val = s.split("=", 1)
        return f'["{key.strip()}"="{val.strip()}"]'
    return f'["{s}"]'


class OverpassClient:
    """Client OpenStreetMap Overpass (POIs par tag sur une zone). Sans clé."""

    def __init__(self, timeout: int = TIMEOUT):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _UA})

    def _run(self, query: str) -> dict[str, Any]:
        last = None
        for url in ENDPOINTS:
            try:
                resp = self._session.post(url, data={"data": query}, timeout=self._timeout)
                if resp.status_code == 200:
                    return resp.json()
                last = f"HTTP {resp.status_code} on {url}"
            except requests.RequestException as e:  # noqa: PERF203
                last = f"{type(e).__name__} on {url}: {e}"
        raise RuntimeError(f"Overpass indisponible ({last})")

    def pois(
        self,
        selector: str,
        *,
        commune: Optional[str] = None,
        departement: Optional[str] = None,
        bbox: Optional[tuple[float, float, float, float]] = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """POIs OSM portant `selector` (ex. 'shop=laundry') sur une zone.

        Zone : exactement l'un de `commune` (code INSEE 5 ch.), `departement`
        (2-3 ch.) ou `bbox` (south, west, north, east). Renvoie `count` + `pois`
        normalisés (osm_type, osm_id, name, lat, lon, postcode, tags).
        """
        given = [x for x in (commune, departement, bbox) if x is not None]
        if len(given) != 1:
            raise ValueError("fournir exactement l'un de commune / departement / bbox")
        clause = _selector_clause(selector)

        if bbox is not None:
            s, w, n, e = bbox
            body = f"nwr{clause}({s},{w},{n},{e});"
        else:
            code = commune or departement
            level = "8" if commune else "6"
            # L'assignation d'aire est un statement autonome, JAMAIS dans un groupe ().
            body = (f'area["ref:INSEE"="{code}"]["admin_level"="{level}"]->.a;'
                    f"nwr{clause}(area.a);")
        query = f"[out:json][timeout:60];{body}out tags center {int(limit)};"

        data = self._run(query)
        pois: list[dict[str, Any]] = []
        for el in data.get("elements", []):
            tags = el.get("tags", {}) or {}
            center = el.get("center") or {}
            lat = el.get("lat", center.get("lat"))
            lon = el.get("lon", center.get("lon"))
            pois.append({
                "osm_type": el.get("type"),
                "osm_id": el.get("id"),
                "name": tags.get("name"),
                "lat": lat,
                "lon": lon,
                "postcode": tags.get("addr:postcode"),
                "tags": tags,
            })
        return {"selector": selector, "count": len(pois), "pois": pois}
