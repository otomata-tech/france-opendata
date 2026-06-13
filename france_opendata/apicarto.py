"""API Carto IGN — cadastre (parcelles), open data.

Source : https://apicarto.ign.fr/api/cadastre (IGN). Pas de clé. Licence Ouverte.

Retourne la/les parcelle(s) cadastrale(s) en un point GPS ou sous une géométrie
GeoJSON : identifiant unique (idu), commune, contenance (m²) et géométrie. Le
test "centroïde du bâtiment dans la parcelle", le scoring foncier et l'estimation
de surface exploitable restent à la charge de l'appelant (logique métier).
"""
from __future__ import annotations

import json
from typing import Any, Optional

import requests


PARCELLE_URL = "https://apicarto.ign.fr/api/cadastre/parcelle"


def _summary(feature: dict[str, Any]) -> dict[str, Any]:
    p = feature.get("properties", {}) or {}
    contenance = p.get("contenance")
    try:
        contenance = float(contenance) if contenance is not None else None
    except (TypeError, ValueError):
        contenance = None
    return {
        "idu": p.get("idu"),
        "commune": p.get("nom_com"),
        "code_insee": p.get("code_insee"),
        "section": p.get("section"),
        "numero": p.get("numero"),
        "contenance_m2": contenance,
        "geometry": feature.get("geometry"),
        "raw": p,
    }


class ApiCartoClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()

    def parcelles_at(self, lat: float, lon: float) -> list[dict[str, Any]]:
        """Parcelles contenant le point (lat, lon), de la plus pertinente à la moins."""
        geom = json.dumps({"type": "Point", "coordinates": [lon, lat]})
        resp = self.session.get(PARCELLE_URL, params={"geom": geom}, timeout=self.timeout)
        resp.raise_for_status()
        return [_summary(f) for f in resp.json().get("features", []) if f.get("geometry")]

    def parcelle_at(self, lat: float, lon: float) -> Optional[dict[str, Any]]:
        """1ère parcelle contenant le point (lat, lon), ou None."""
        parcelles = self.parcelles_at(lat, lon)
        return parcelles[0] if parcelles else None

    def parcelles_by_geom(self, geometry: dict[str, Any]) -> list[dict[str, Any]]:
        """Parcelles intersectant une géométrie GeoJSON arbitraire (Polygon, etc.)."""
        resp = self.session.get(PARCELLE_URL, params={"geom": json.dumps(geometry)}, timeout=self.timeout)
        resp.raise_for_status()
        return [_summary(f) for f in resp.json().get("features", []) if f.get("geometry")]
