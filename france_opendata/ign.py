"""IGN Géoplateforme — service de navigation (isochrone), open data sans clé.

Source : `https://data.geopf.fr/navigation/isochrone` (moteur Valhalla sur le
graphe BD TOPO). Pour un point + un budget de temps (ou de distance) + un mode
(piéton / voiture), renvoie le **polygone de la zone atteignable** (GeoJSON) —
la « zone de chalandise » / isochrone.

Usage type : cartographier ce qui est joignable en ≤ N minutes à pied d'un point
(implantation retail : qui est à moins de 10 min d'une laverie, d'un commerce…).
Les questions business (population dans la zone, concurrents couverts) se croisent
côté appelant — la lib ne renvoie que la géométrie IGN.
"""
from __future__ import annotations

from typing import Any, Optional

import requests

from .geo import bbox_of_geom, centroid_of_geom

ISOCHRONE_URL = "https://data.geopf.fr/navigation/isochrone"
TIMEOUT = 30

_PROFILES = {"pied", "pedestrian", "voiture", "car"}
_PROFILE_ALIAS = {"pied": "pedestrian", "voiture": "car"}


class IgnClient:
    """Client IGN Géoplateforme navigation (isochrone). Sans clé."""

    def __init__(self, timeout: int = TIMEOUT):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "france-opendata"})

    def isochrone(
        self,
        lat: float,
        lon: float,
        *,
        minutes: Optional[float] = None,
        metres: Optional[int] = None,
        profile: str = "pedestrian",
        direction: str = "departure",
    ) -> dict[str, Any]:
        """Zone atteignable depuis (lat, lon) en `minutes` (ou `metres`).

        `profile` : 'pedestrian'/'pied' ou 'car'/'voiture'. `direction` :
        'departure' (zone qu'on atteint DEPUIS le point) ou 'arrival' (zone
        d'où l'on atteint le point) — asymétrique en voiture (sens uniques).

        Retour : `geometry` (Polygon GeoJSON de la zone), plus `centroid` et
        `bbox` dérivés, et l'écho des paramètres (mode, budget). Exactement l'un
        de `minutes`/`metres` doit être fourni.
        """
        if (minutes is None) == (metres is None):
            raise ValueError("fournir exactement l'un de minutes / metres")
        prof = _PROFILE_ALIAS.get(profile, profile)
        if prof not in ("pedestrian", "car"):
            raise ValueError(f"profile invalide: {profile!r} (pied/pedestrian ou voiture/car)")
        if minutes is not None:
            cost_type, cost_value = "time", int(round(minutes * 60))
        else:
            cost_type, cost_value = "distance", int(metres)

        params = {
            "point": f"{lon},{lat}",
            "resource": "bdtopo-valhalla",
            "costType": cost_type,
            "costValue": cost_value,
            "profile": prof,
            "direction": direction,
            "geometryFormat": "geojson",
        }
        resp = self._session.get(ISOCHRONE_URL, params=params, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        geom = data.get("geometry")
        clon, clat = centroid_of_geom(geom)  # geo helper rend (x=lon, y=lat)
        bbox, _ = bbox_of_geom(geom, margin_m=0)
        return {
            "geometry": geom,
            "centroid": {"lat": clat, "lon": clon},
            "bbox": bbox,
            "profile": prof,
            "direction": direction,
            "minutes": minutes,
            "metres": metres,
        }
