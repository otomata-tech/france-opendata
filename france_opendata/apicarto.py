"""Cadastre (parcelles) IGN — open data, sans clé.

Source : WFS Géoplateforme `CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle`
(`data.geopf.fr/wfs/ov`), Licence Ouverte. Porté de l'ancienne API Carto IGN
(`apicarto.ign.fr/api/cadastre`, en cours de dépréciation par IGN au profit de
la Géoplateforme) — schéma de propriétés et géométrie identiques.

Retourne la/les parcelle(s) cadastrale(s) en un point GPS ou sous une géométrie
GeoJSON : identifiant unique (idu), commune, contenance (m²) et géométrie. Le
test "centroïde du bâtiment dans la parcelle", le scoring foncier et l'estimation
de surface exploitable restent à la charge de l'appelant (logique métier).

Axes : GeoJSON = `[lon, lat]` ; le WFS 2.0 en EPSG:4326 attend le filtre en
`lat lon` (ordre d'axe officiel) mais renvoie la géométrie en `[lon, lat]`
standard — d'où le swap en entrée seulement (`_geometry_to_wkt`), passthrough
en sortie (`_summary`).
"""
from __future__ import annotations

from typing import Any, Optional

import requests


WFS_URL = "https://data.geopf.fr/wfs/ov"
LAYER = "CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle"
PAGE_SIZE = 1000
MAX_PAGES = 10


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


# ─── GeoJSON [lon,lat] → WKT [lat lon] pour le filtre CQL (axe EPSG:4326) ─────

def _ring_wkt(ring: list) -> str:
    return "(" + ", ".join(f"{lat} {lon}" for lon, lat, *_ in ring) + ")"


def _polygon_wkt(poly: list) -> str:
    return "(" + ", ".join(_ring_wkt(r) for r in poly) + ")"


def _geometry_to_wkt(geometry: dict[str, Any]) -> str:
    t = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if t == "Point":
        lon, lat = coords[:2]
        return f"POINT({lat} {lon})"
    if t == "MultiPoint":
        return "MULTIPOINT(" + ", ".join(f"{c[1]} {c[0]}" for c in coords) + ")"
    if t == "LineString":
        return "LINESTRING(" + ", ".join(f"{c[1]} {c[0]}" for c in coords) + ")"
    if t == "Polygon":
        return "POLYGON" + _polygon_wkt(coords)
    if t == "MultiPolygon":
        return "MULTIPOLYGON(" + ", ".join(_polygon_wkt(p) for p in coords) + ")"
    raise ValueError(f"type de géométrie non supporté pour la requête cadastre : {t!r}")


class ApiCartoClient:
    """Parcelles cadastrales IGN via le WFS Géoplateforme (PARCELLAIRE_EXPRESS)."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()

    def _query(self, wkt: str) -> list[dict[str, Any]]:
        features: list[dict[str, Any]] = []
        start = 0
        for _ in range(MAX_PAGES):
            resp = self.session.get(
                WFS_URL,
                params={
                    "SERVICE": "WFS",
                    "VERSION": "2.0.0",
                    "REQUEST": "GetFeature",
                    "TYPENAMES": LAYER,
                    "OUTPUTFORMAT": "application/json",
                    "SRSNAME": "EPSG:4326",
                    "CQL_FILTER": f"INTERSECTS(geom,{wkt})",
                    "COUNT": str(PAGE_SIZE),
                    "STARTINDEX": str(start),
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            page = data.get("features") or []
            features.extend(page)
            matched = int(data.get("numberMatched") or len(features))
            if len(page) < PAGE_SIZE or len(features) >= matched:
                break
            start += PAGE_SIZE
        return [_summary(f) for f in features if f.get("geometry")]

    def parcelles_at(self, lat: float, lon: float) -> list[dict[str, Any]]:
        """Parcelles contenant le point (lat, lon), de la plus pertinente à la moins."""
        return self._query(_geometry_to_wkt({"type": "Point", "coordinates": [lon, lat]}))

    def parcelle_at(self, lat: float, lon: float) -> Optional[dict[str, Any]]:
        """1ère parcelle contenant le point (lat, lon), ou None."""
        parcelles = self.parcelles_at(lat, lon)
        return parcelles[0] if parcelles else None

    def parcelles_by_geom(self, geometry: dict[str, Any]) -> list[dict[str, Any]]:
        """Parcelles intersectant une géométrie GeoJSON arbitraire (Polygon, etc.)."""
        return self._query(_geometry_to_wkt(geometry))
