"""BAN — Base Adresse Nationale (géocodage open data).

Source : https://api-adresse.data.gouv.fr (Etalab). Pas de clé. Licence Ouverte.

Géocode une adresse texte → coordonnées + label canonique + code commune INSEE,
et l'inverse (reverse). Le label BAN sert de clé d'adresse canonique : deux
sources qui écrivent "RTE DE CONDE" et "ROUTE DE CONDÉ" convergent sur le même
point. La normalisation déterministe de repli et l'expansion arrondissement →
commune (Paris/Lyon/Marseille) sont du ressort de l'appelant (logique métier).
"""
from __future__ import annotations

from typing import Any, Optional

import requests


BASE_URL = "https://api-adresse.data.gouv.fr"


def _feature(f: dict[str, Any]) -> dict[str, Any]:
    props = f.get("properties", {}) or {}
    coords = (f.get("geometry", {}) or {}).get("coordinates") or [None, None]
    return {
        "label": props.get("label"),
        "score": props.get("score"),
        "lon": coords[0],
        "lat": coords[1],
        "type": props.get("type"),
        "citycode": props.get("citycode"),
        "city": props.get("city"),
        "postcode": props.get("postcode"),
        "context": props.get("context"),
        "raw": props,
    }


class BanClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()

    def search(
        self,
        q: str,
        *,
        limit: int = 5,
        autocomplete: bool = False,
        type: Optional[str] = None,
        postcode: Optional[str] = None,
        citycode: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Géocode `q` → liste de candidats (label, score, lon, lat, citycode…).

        `type` ∈ {housenumber, street, locality, municipality} pour filtrer.
        `citycode`/`postcode` restreignent à une commune.
        """
        params: dict[str, Any] = {"q": q, "limit": limit,
                                  "autocomplete": "1" if autocomplete else "0"}
        if type:
            params["type"] = type
        if postcode:
            params["postcode"] = postcode
        if citycode:
            params["citycode"] = citycode
        resp = self.session.get(f"{BASE_URL}/search/", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return [_feature(f) for f in resp.json().get("features", [])]

    def geocode(self, q: str, **kwargs: Any) -> Optional[dict[str, Any]]:
        """Meilleur candidat pour `q` (le 1er, plus haut score), ou None."""
        results = self.search(q, limit=1, **kwargs)
        return results[0] if results else None

    def reverse(self, lat: float, lon: float, *, type: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Adresse la plus proche d'un point (lat, lon), ou None."""
        params: dict[str, Any] = {"lat": lat, "lon": lon}
        if type:
            params["type"] = type
        resp = self.session.get(f"{BASE_URL}/reverse/", params=params, timeout=self.timeout)
        resp.raise_for_status()
        features = resp.json().get("features", [])
        return _feature(features[0]) if features else None
