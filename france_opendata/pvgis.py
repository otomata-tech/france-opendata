"""PVGIS (JRC, Commission Européenne) — productible solaire annuel.

Source : https://re.jrc.ec.europa.eu/api (PVGIS v5.3, base SARAH-3). Pas de clé.

Pour un point GPS + une puissance crête (kWc) installée, renvoie le productible
annuel (kWh), l'irradiance, les pertes et l'inclinaison/azimut optimaux. Avec
`optimalangles=1` + `mountingplace=building`, PVGIS choisit tilt+azimut optimaux
pour toiture. Les hypothèses business (tarif autoconso €/kWh, facteur d'émission
CO₂, € économisés) sont du ressort de l'appelant — la lib ne renvoie que la
donnée physique PVGIS.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import requests


PVCALC_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"


class PvgisClient:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()

    def productible(
        self,
        lat: float,
        lon: float,
        kwc: float,
        *,
        loss: float = 14.0,
        optimal_angles: bool = True,
        mounting: str = "building",
        pv_tech: str = "crystSi",
    ) -> Optional[dict[str, Any]]:
        """Productible annuel pour (lat, lon, kwc). None si inputs invalides ou API KO.

        Retourne `{productible_kwh_an, irradiance_kwh_m2_an, pertes_pct,
        optimal_slope_deg, optimal_azimuth_deg, inputs}`.
        """
        if kwc < 1 or not math.isfinite(lat) or not math.isfinite(lon):
            return None
        params = {
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "peakpower": round(kwc, 1),
            "loss": loss,
            "optimalangles": 1 if optimal_angles else 0,
            "mountingplace": mounting,
            "pvtechchoice": pv_tech,
            "outputformat": "json",
        }
        resp = self.session.get(PVCALC_URL, params=params, timeout=self.timeout,
                                headers={"Accept": "application/json"})
        if not resp.ok:
            return None
        data = resp.json()
        fixed = (((data.get("outputs") or {}).get("totals") or {}).get("fixed") or {})
        e_y = fixed.get("E_y")
        if not isinstance(e_y, (int, float)):
            return None
        slope = (((data.get("inputs") or {}).get("mounting_system") or {})
                 .get("fixed") or {}).get("slope", {}).get("value")
        azimuth = (((data.get("inputs") or {}).get("mounting_system") or {})
                   .get("fixed") or {}).get("azimuth", {}).get("value")
        return {
            "productible_kwh_an": round(e_y),
            "irradiance_kwh_m2_an": round(fixed.get("H(i)_y") or 0),
            "pertes_pct": round(abs(fixed.get("l_total") or 0), 2),
            "optimal_slope_deg": slope if isinstance(slope, (int, float)) else None,
            "optimal_azimuth_deg": azimuth if isinstance(azimuth, (int, float)) else None,
            "inputs": {"peakpower_kwc": round(kwc, 1), "loss_pct": loss,
                       "optimal_angles": optimal_angles, "mounting": mounting, "pv_tech": pv_tech},
        }
