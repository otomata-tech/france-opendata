"""DPE — Diagnostics de Performance Énergétique (ADEME, open data).

Source : API DataFair de l'ADEME, dataset `dpe03existant` (logements existants
depuis juillet 2021, ~15 M diagnostics géocodés BAN).
  https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines
Sans clé. Licence Ouverte.

Principe (comme DVF) : **on expose la donnée brute, on ne filtre pas à la place
de l'agent.** Un DPE = une ligne, avec étiquette énergie/GES, conso, surface,
année, adresse BAN, géoloc. Filtres (`type_batiment`, `etiquette`, surface)
optionnels — absents = tout passe.

⚠️ Pas d'appariement fiable DPE↔vente DVF en copropriété : un immeuble a N DPE et
M ventes sans clé commune (DVF = parcelle+prix, DPE = adresse BAN+étiquette). Le
rapprochement (par proximité + surface) est un travail d'orchestration côté
consommateur, fiable seulement pour les maisons (mono-logement) — pas ici.
"""
from __future__ import annotations

from typing import Any, Optional

import requests


API_BASE = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant"
BAN_URL = "https://api-adresse.data.gouv.fr/search/"

# Champs exposés (le dataset en a ~230 ; on sélectionne l'utile pour limiter le payload).
_SELECT = ",".join([
    "etiquette_dpe", "etiquette_ges", "conso_5_usages_par_m2_ep",
    "surface_habitable_logement", "annee_construction", "type_batiment",
    "adresse_ban", "code_postal_ban", "nom_commune_ban", "code_insee_ban",
    "identifiant_ban", "date_etablissement_dpe", "_geopoint",
])


class DpeClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()

    def geocode(self, adresse: str) -> Optional[dict[str, Any]]:
        """Géocode une adresse via la BAN. {lon, lat, code_commune, label} | None."""
        r = self.session.get(BAN_URL, params={"q": adresse, "limit": 1}, timeout=self.timeout)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if not feats:
            return None
        f = feats[0]
        lon, lat = f["geometry"]["coordinates"]
        p = f["properties"]
        return {"lon": lon, "lat": lat, "code_commune": p.get("citycode"), "label": p.get("label")}

    def _lines(self, params: dict[str, Any]) -> dict[str, Any]:
        r = self.session.get(f"{API_BASE}/lines", params={**params, "select": _SELECT}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _qs(type_batiment: Optional[str], etiquette: Optional[str], extra: Optional[str] = None) -> Optional[str]:
        clauses = [extra] if extra else []
        if type_batiment:
            clauses.append(f'type_batiment:"{type_batiment}"')
        if etiquette:
            clauses.append(f'etiquette_dpe:"{etiquette}"')
        return " AND ".join(clauses) if clauses else None

    def _normalize(self, rows: list[dict[str, Any]], surface_min, surface_max) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in rows:
            surf = r.get("surface_habitable_logement")
            if surface_min is not None and (surf is None or surf < surface_min):
                continue
            if surface_max is not None and (surf is None or surf > surface_max):
                continue
            lat = lon = None
            gp = r.get("_geopoint")
            if gp and "," in gp:
                try:
                    lat, lon = (round(float(x), 6) for x in gp.split(","))
                except ValueError:
                    lat = lon = None
            item = {
                "etiquette_dpe": r.get("etiquette_dpe"),
                "etiquette_ges": r.get("etiquette_ges"),
                "conso_ep_kwh_m2_an": r.get("conso_5_usages_par_m2_ep"),
                "surface_habitable": surf,
                "annee_construction": r.get("annee_construction"),
                "type_batiment": r.get("type_batiment"),
                "adresse": r.get("adresse_ban"),
                "code_postal": r.get("code_postal_ban"),
                "commune": r.get("nom_commune_ban"),
                "code_commune": r.get("code_insee_ban"),
                "identifiant_ban": r.get("identifiant_ban"),
                "date_dpe": r.get("date_etablissement_dpe"),
                "longitude": lon,
                "latitude": lat,
            }
            if r.get("_geo_distance") is not None:
                item["distance_m"] = round(r["_geo_distance"])
            out.append(item)
        return out

    def by_address(
        self,
        adresse: str,
        radius_m: int = 200,
        type_batiment: Optional[str] = None,
        etiquette: Optional[str] = None,
        surface_min: Optional[float] = None,
        surface_max: Optional[float] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """DPE bruts autour d'une adresse (géocode BAN + rayon), plus proches d'abord.

        Args:
            adresse: adresse libre.
            radius_m: rayon en mètres.
            type_batiment: filtre OPTIONNEL ("maison" | "appartement" | "immeuble").
            etiquette: filtre OPTIONNEL sur l'étiquette DPE (A..G).
            surface_min/max: filtres OPTIONNELS sur la surface habitable m².
            limit: nb max de DPE (les plus proches).
        """
        geo = self.geocode(adresse)
        if not geo:
            return {"adresse": adresse, "error": "geocode_failed", "dpe": [], "count": 0}
        params: dict[str, Any] = {
            "geo_distance": f"{geo['lon']},{geo['lat']},{radius_m}",
            "size": min(limit, 1000),
        }
        qs = self._qs(type_batiment, etiquette)
        if qs:
            params["qs"] = qs
        data = self._lines(params)
        rows = self._normalize(data.get("results", []), surface_min, surface_max)
        rows.sort(key=lambda d: d.get("distance_m") if d.get("distance_m") is not None else 1e9)
        return {
            "adresse_geocodee": geo["label"],
            "code_commune": geo["code_commune"],
            "radius_m": radius_m,
            "count": len(rows),
            "dpe": rows[:limit],
        }

    def commune(
        self,
        code_commune: str,
        type_batiment: Optional[str] = None,
        etiquette: Optional[str] = None,
        surface_min: Optional[float] = None,
        surface_max: Optional[float] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """DPE bruts d'une commune (code INSEE), plus récents d'abord."""
        data = self._lines({
            "qs": self._qs(type_batiment, etiquette, extra=f'code_insee_ban:"{code_commune}"'),
            "sort": "-date_etablissement_dpe",
            "size": min(limit, 1000),
        })
        rows = self._normalize(data.get("results", []), surface_min, surface_max)
        return {
            "code_commune": code_commune,
            "count": len(rows),
            "total_commune": data.get("total"),
            "dpe": rows[:limit],
        }

    def stats(self, code_commune: str, type_batiment: Optional[str] = None) -> dict[str, Any]:
        """Répartition des étiquettes DPE (A..G) d'une commune — vue agrégée."""
        params = {"field": "etiquette_dpe", "agg_size": 10}
        qs = self._qs(type_batiment, None, extra=f'code_insee_ban:"{code_commune}"')
        if qs:
            params["qs"] = qs
        r = self.session.get(f"{API_BASE}/values_agg", params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        repartition = {a.get("value"): a.get("total") for a in data.get("aggs", []) if a.get("value")}
        return {
            "code_commune": code_commune,
            "type_batiment": type_batiment,
            "total": data.get("total"),
            "repartition_etiquettes": dict(sorted(repartition.items())),
        }
