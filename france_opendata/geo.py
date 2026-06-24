"""Utilitaires géométriques GeoJSON — sans dépendance (stdlib `math` uniquement).

Helpers génériques partagés par les connecteurs spatiaux (cadastre, GPU, BDTOPO) :
bounding box élargie d'une géométrie, centroïde grossier (moyenne des sommets).
Travaillent en degrés EPSG:4326.
"""
from __future__ import annotations

import math
from typing import Any, Optional


def _walk_coords(coords: Any, xs: list, ys: list) -> None:
    """Parcourt récursivement des coordonnées GeoJSON (Point→MultiPolygon) et
    accumule les longitudes dans `xs`, latitudes dans `ys`."""
    if isinstance(coords, (list, tuple)):
        if coords and isinstance(coords[0], (int, float)):
            xs.append(coords[0])
            ys.append(coords[1])
        else:
            for c in coords:
                _walk_coords(c, xs, ys)


def centroid_of_geom(geometry: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    """Centroïde grossier (moyenne des sommets) d'une géométrie GeoJSON.

    Suffisant pour estimer une distance approximative entre parcelles ; ce n'est
    PAS le centroïde de surface. Retourne (None, None) si la géométrie est vide.
    """
    xs: list[float] = []
    ys: list[float] = []
    _walk_coords((geometry or {}).get("coordinates"), xs, ys)
    if not xs:
        return None, None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def bbox_of_geom(geometry: Optional[dict], margin_m: float) -> tuple[Optional[dict], tuple]:
    """Bounding box (Polygon GeoJSON) d'une géométrie, élargie de `margin_m` mètres.

    Returns (polygon_geojson | None, centroid). Le polygone est un dict GeoJSON
    prêt à passer à un client cadastre (`ApiCartoClient.parcelles_by_geom`).
    `centroid` = (cx, cy) moyenne des sommets, (None, None) si géométrie vide.
    """
    xs: list[float] = []
    ys: list[float] = []
    _walk_coords((geometry or {}).get("coordinates"), xs, ys)
    if not xs:
        return None, (None, None)
    lat_c = (min(ys) + max(ys)) / 2
    dlat = margin_m / 111_320
    dlon = margin_m / (111_320 * max(0.1, math.cos(math.radians(lat_c))))
    xmin, xmax = min(xs) - dlon, max(xs) + dlon
    ymin, ymax = min(ys) - dlat, max(ys) + dlat
    ring = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]
    centroid = (sum(xs) / len(xs), sum(ys) / len(ys))
    return {"type": "Polygon", "coordinates": [ring]}, centroid
