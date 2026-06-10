"""BDTOPO V3 — bâti existant dans une parcelle (IGN, open data).

Source : WFS Géoplateforme `BDTOPO_V3:batiment` (data.geopf.fr), sans clé.
Use case foncier : mesurer l'emprise au sol déjà bâtie d'une parcelle cadastrale
pour repérer la sous-exploitation (CES réel faible en zone urbaine dense = signal
de développement). Porté du service geo-foncier de GR (bdtopo-buildings.ts),
sans le volet PV (PVGIS).

Géométrie en pur Python (pas de shapely) : shoelace en mètres locaux pour les
aires, ray casting pour le test « centroïde dans la parcelle ». Précision
suffisante pour des emprises bâties à l'échelle parcellaire (< 1 %).
"""
from __future__ import annotations

import math
from typing import Any, Optional

import requests

WFS_URL = "https://data.geopf.fr/wfs/ov"
LAYER = "BDTOPO_V3:batiment"
PAGE_SIZE = 2000
MAX_PAGES = 6
BBOX_MARGIN_M = 10  # marge autour de la parcelle pour la requête bbox


# ─── géométrie (GeoJSON Polygon / MultiPolygon, coords [lon, lat]) ───────────

def _rings(geometry: dict) -> list[tuple[list, list[list]]]:
    """[(anneau extérieur, [trous])] pour Polygon ou MultiPolygon."""
    t, coords = geometry.get("type"), geometry.get("coordinates") or []
    if t == "Polygon":
        polys = [coords]
    elif t == "MultiPolygon":
        polys = coords
    else:
        return []
    return [(p[0], list(p[1:])) for p in polys if p]


def _meters_factors(lat0: float) -> tuple[float, float]:
    return 111_320.0 * math.cos(math.radians(lat0)), 110_540.0


def _ring_area_m2(ring: list, lat0: float) -> float:
    """Shoelace sur l'anneau projeté en mètres locaux (équirectangulaire)."""
    if len(ring) < 4:
        return 0.0
    mx, my = _meters_factors(lat0)
    s = 0.0
    # coords parfois 3D ([lon, lat, z]) selon la source (BDTOPO) → on tronque.
    for p1, p2 in zip(ring, ring[1:]):
        (x1, y1), (x2, y2) = p1[:2], p2[:2]
        s += (x1 * mx) * (y2 * my) - (x2 * mx) * (y1 * my)
    return abs(s) / 2.0


def area_m2(geometry: dict, lat0: Optional[float] = None) -> float:
    """Aire d'un (Multi)Polygon en m² (extérieurs − trous)."""
    rings = _rings(geometry)
    if not rings:
        return 0.0
    if lat0 is None:
        lat0 = rings[0][0][0][:2][1]
    total = 0.0
    for exterior, holes in rings:
        total += _ring_area_m2(exterior, lat0)
        for h in holes:
            total -= _ring_area_m2(h, lat0)
    return max(total, 0.0)


def centroid(geometry: dict) -> Optional[tuple[float, float]]:
    """(lon, lat) — moyenne des sommets de l'anneau extérieur le plus grand."""
    rings = _rings(geometry)
    if not rings:
        return None
    exterior = max((r[0] for r in rings), key=len)
    pts = exterior[:-1] if exterior[0] == exterior[-1] else exterior
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _point_in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    for p1, p2 in zip(ring, ring[1:]):
        (x1, y1), (x2, y2) = p1[:2], p2[:2]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    for exterior, holes in _rings(geometry):
        if _point_in_ring(lon, lat, exterior) and not any(
            _point_in_ring(lon, lat, h) for h in holes
        ):
            return True
    return False


def bbox_of(geometry: dict, margin_m: float = 0.0) -> Optional[tuple[float, float, float, float]]:
    """(lon_min, lat_min, lon_max, lat_max), marge en mètres."""
    lons: list[float] = []
    lats: list[float] = []

    def _walk(c):
        if isinstance(c, (list, tuple)):
            if len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2]):
                lons.append(c[0])
                lats.append(c[1])
            else:
                for sub in c:
                    _walk(sub)

    _walk((geometry or {}).get("coordinates"))
    if not lons:
        return None
    mx, my = _meters_factors(sum(lats) / len(lats))
    dlon, dlat = margin_m / mx, margin_m / my
    return (min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat)


# ─── client ──────────────────────────────────────────────────────────────────

class BdTopoClient:
    """Bâtiments BDTOPO V3 dans une emprise, et synthèse du bâti d'une parcelle."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self.session = requests.Session()

    def batiments_bbox(self, bbox: tuple[float, float, float, float]) -> list[dict]:
        """Features bâtiment dans la bbox (lon_min, lat_min, lon_max, lat_max), paginé."""
        features: list[dict] = []
        start = 0
        for _ in range(MAX_PAGES):
            r = self.session.get(
                WFS_URL,
                params={
                    "SERVICE": "WFS",
                    "VERSION": "2.0.0",
                    "REQUEST": "GetFeature",
                    "TYPENAMES": LAYER,
                    "OUTPUTFORMAT": "application/json",
                    "BBOX": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]},CRS:84",
                    "COUNT": str(PAGE_SIZE),
                    "STARTINDEX": str(start),
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            page = data.get("features") or []
            features.extend(page)
            matched = int(data.get("numberMatched") or len(features))
            if len(page) < PAGE_SIZE or len(features) >= matched:
                break
            start += PAGE_SIZE
        return features

    def bati_parcelle(
        self, parcelle_geometry: dict, contenance_m2: Optional[int] = None
    ) -> dict[str, Any]:
        """Synthèse du bâti d'une parcelle : emprise au sol, CES réel, usages, hauteurs.

        Un bâtiment est rattaché à la parcelle si son centroïde y tombe (convention
        BDTOPO : un bâtiment chevauchant est compté une seule fois). `ces_reel` n'est
        calculé que si `contenance_m2` est fourni (cadastre).
        """
        bbox = bbox_of(parcelle_geometry, margin_m=BBOX_MARGIN_M)
        if bbox is None:
            raise ValueError("géométrie de parcelle vide")
        lat0 = (bbox[1] + bbox[3]) / 2

        batiments: list[dict] = []
        for feat in self.batiments_bbox(bbox):
            geom = feat.get("geometry")
            if not geom:
                continue
            c = centroid(geom)
            if c is None or not point_in_geometry(c[0], c[1], parcelle_geometry):
                continue
            p = feat.get("properties") or {}
            emprise = round(area_m2(geom, lat0))
            if emprise <= 0:
                continue
            hauteur = p.get("hauteur")
            batiments.append(
                {
                    "cleabs": p.get("cleabs"),
                    "usage": p.get("usage_1"),
                    "nature": p.get("nature") or None,
                    "hauteur_m": float(hauteur) if isinstance(hauteur, (int, float)) else None,
                    "emprise_m2": emprise,
                    "construction_legere": bool(p.get("construction_legere")),
                }
            )

        batiments.sort(key=lambda b: -b["emprise_m2"])
        emprise_totale = sum(b["emprise_m2"] for b in batiments)
        par_usage: dict[str, dict[str, int]] = {}
        for b in batiments:
            u = b["usage"] or "(inconnu)"
            slot = par_usage.setdefault(u, {"nb": 0, "emprise_m2": 0})
            slot["nb"] += 1
            slot["emprise_m2"] += b["emprise_m2"]
        hauteurs = [b["hauteur_m"] for b in batiments if b["hauteur_m"]]

        return {
            "nb_batiments": len(batiments),
            "emprise_batie_m2": emprise_totale,
            "contenance_m2": contenance_m2,
            "ces_reel": (
                round(emprise_totale / contenance_m2, 3)
                if contenance_m2 and contenance_m2 > 0
                else None
            ),
            "hauteur_max_m": max(hauteurs) if hauteurs else None,
            "dont_constructions_legeres": sum(
                1 for b in batiments if b["construction_legere"]
            ),
            "par_usage": par_usage,
            "batiments": batiments,
            "source": "IGN BDTOPO V3 (WFS data.geopf.fr), open data",
        }
