"""Quartiers Prioritaires de la politique de la Ville (QPV) — dataset national public.

Source : `public.opendatasoft.com` (dataset national des QPV, géométrie incluse, sans
clé). Le filtre géographique (`within_distance`) est fait **côté serveur** → pas de
dépendance géométrique locale.

Usage type : éligibilité GÉOGRAPHIQUE à la TVA 5,5 % accession (être dans un QPV ou
dans son périmètre de 300 m). ⚠️ Ce n'est que la condition géographique : la TVA 5,5 %
exige AUSSI un plafond de prix de vente et un plafond de ressources acheteur — non
portés ici (interprétation côté appelant).
"""
from __future__ import annotations

from typing import Any

import requests

_DATASET = "quartiers-prioritaires-de-la-politique-de-la-ville-qpv"
BASE_URL = (f"https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
            f"{_DATASET}/records")
TIMEOUT = 25
_FIELDS = ("code_qp", "nom_qp", "commune_qp", "code_insee", "nom_epci")


class QpvClient:
    """Client Quartiers Prioritaires de la Ville (Opendatasoft national). Sans clé."""

    def __init__(self, timeout: int = TIMEOUT):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "france-opendata"})

    def _query(self, where: str, limit: int = 20) -> tuple[list[dict], int]:
        resp = self._session.get(BASE_URL, params={"where": where, "limit": limit},
                                 timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        rows = [{k: row.get(k) for k in _FIELDS} for row in data.get("results", [])]
        return rows, data.get("total_count") or 0

    def by_commune(self, code_insee: str) -> dict[str, Any]:
        """QPV d'une commune (par code INSEE). `nb_qpv`=0 → commune sans QPV."""
        rows, total = self._query(f'code_insee="{code_insee}"')
        return {"code_insee": code_insee, "nb_qpv": total, "qpv": rows,
                "qpv_dans_la_commune": bool(total)}

    def near_point(self, lon: float, lat: float, radius_m: int = 300) -> dict[str, Any]:
        """QPV dont la géométrie est à moins de `radius_m` mètres du point (lon, lat).

        `eligible_geo`=True si au moins un QPV est dans le rayon (condition
        géographique TVA 5,5 % ; 300 m = périmètre réglementaire par défaut).
        """
        where = f"within_distance(geo_shape, geom'POINT({lon} {lat})', {radius_m}m)"
        rows, total = self._query(where)
        return {"point": [lon, lat], "radius_m": radius_m, "nb_qpv": total,
                "qpv": rows, "eligible_geo": bool(total)}
