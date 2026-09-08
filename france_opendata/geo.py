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


# --- Lambert 93 (EPSG:2154) --------------------------------------------------
#
# Pourquoi ici : le stock SIRENE porte les coordonnées d'établissement en Lambert 93
# (`lambert_x`/`lambert_y`, projection légale française), quand la BAN et tous les
# portails open data rendent du WGS84 en degrés. Rapprocher un site d'un établissement
# suppose donc de ramener les deux dans le même plan. `pyproj` ferait le travail, mais
# la lib tient sur `requests` seul et une conique conforme de Lambert tient en trente
# lignes de `math` — la dépendance coûterait plus que le calcul.
#
# Conique conforme sécante à deux parallèles (LCC 2SP), ellipsoïde GRS80, tels que
# définis pour EPSG:2154.

_GRS80_A = 6378137.0
_GRS80_E = 0.081819191042816  # première excentricité, 1/f = 298.257222101
_L93_LON0 = 3.0               # méridien d'origine (Greenwich)
_L93_LAT0 = 46.5              # latitude d'origine
_L93_LAT1 = 44.0              # 1er parallèle automécoïque
_L93_LAT2 = 49.0              # 2e parallèle automécoïque
_L93_X0 = 700000.0
_L93_Y0 = 6600000.0


def _lcc_t(phi: float, e: float) -> float:
    s = math.sin(phi)
    return math.tan(math.pi / 4 - phi / 2) / ((1 - e * s) / (1 + e * s)) ** (e / 2)


def _lcc_m(phi: float, e: float) -> float:
    s = math.sin(phi)
    return math.cos(phi) / math.sqrt(1 - e * e * s * s)


def lambert93(lat: float, lon: float) -> tuple[float, float]:
    """WGS84 (degrés) → Lambert 93 (mètres). Rend `(x, y)`.

    Sert à comparer un point géocodé (BAN, en degrés) aux coordonnées d'établissement
    du stock SIRENE (en Lambert 93). Exact au mètre, ce qui est deux ordres de grandeur
    sous la précision des deux sources qu'on rapproche.
    """
    e = _GRS80_E
    phi = math.radians(lat)
    phi0, phi1, phi2 = (math.radians(x) for x in (_L93_LAT0, _L93_LAT1, _L93_LAT2))
    m1, m2 = _lcc_m(phi1, e), _lcc_m(phi2, e)
    t1, t2 = _lcc_t(phi1, e), _lcc_t(phi2, e)
    n = math.log(m1 / m2) / math.log(t1 / t2)
    f = m1 / (n * t1 ** n)
    r0 = _GRS80_A * f * _lcc_t(phi0, e) ** n
    r = _GRS80_A * f * _lcc_t(phi, e) ** n
    theta = n * math.radians(lon - _L93_LON0)
    return (_L93_X0 + r * math.sin(theta), _L93_Y0 + r0 - r * math.cos(theta))


def distance_lambert_m(
    lat: float, lon: float, x: Optional[float], y: Optional[float]
) -> Optional[float]:
    """Distance en mètres entre un point WGS84 et un point déjà en Lambert 93.

    `x`/`y` viennent tels quels du stock SIRENE — souvent en chaînes, parfois absents
    (un établissement non géolocalisé). Rend `None` plutôt que zéro dans ce cas : une
    distance inconnue n'est pas une distance nulle, et c'est la confusion qui ferait
    rapprocher n'importe quoi.
    """
    if x is None or y is None or x == "" or y == "":
        return None
    try:
        ex, ey = float(x), float(y)
    except (TypeError, ValueError):
        return None
    px, py = lambert93(lat, lon)
    return math.hypot(px - ex, py - ey)
