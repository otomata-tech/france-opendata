"""INSEE Mélodi — données locales par commune (open data, SANS clé).

API de diffusion INSEE Mélodi : `https://api.insee.fr/melodi/data/{DATASET}?GEO=COM-{insee}`.
⚠️ Ne PAS envoyer de clé Sirene (scopée Sirene → 401) : Mélodi est ouvert.

Expose les agrégats communaux du Recensement (RP) et de Filosofi :
- population (RP, 2011/2016/2022),
- familles par type (couples avec/sans enfant, monoparentales),
- ménages d'une personne,
- revenus (médiane niveau de vie, taux de pauvreté),
- logement (résidences principales/vacants/secondaires, statut d'occupation).

Mapping des codes (TFN, TSH…) confirmé par recoupement des effectifs nationaux INSEE,
jamais deviné — donnée absente = renvoyée à null.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

BASE_URL = "https://api.insee.fr/melodi/data"
TIMEOUT = 30
# Backoff entre tentatives (s) sur flakiness AMONT INSEE : l'API Mélodi renvoie par
# intermittence des 5xx / resets sur tous les blocs à la fois (#194-195), non corrélés
# à la charge — un retry les absorbe de façon transparente. Un 4xx (territoire/requête
# invalide) n'est PAS retryé.
_RETRY_BACKOFF = (0.5, 1.5, 3.0)

# Type de famille (TFN) — confirmé par match des effectifs nationaux INSEE FAM1 2022.
_TFN = {
    "11": "monoparentale_homme",
    "12": "monoparentale_femme",
    "21": "couple_sans_enfant",
    "22": "couple_avec_enfant",
}
# Statut d'occupation du logement (TSH), résidences principales.
_TSH = {
    "100": "proprietaires",
    "211": "locataire_prive_vide",
    "212_222": "locataire_meuble",
    "221": "locataire_social_hlm",
    "300": "loge_gratuit",
}


def _geo_ref(insee: str) -> str:
    """Réf. territoire Mélodi pour un code INSEE. Paris (75101-75120), Lyon
    (69381-69389) et Marseille (13201-13216) sont des ARRONDISSEMENTS MUNICIPAUX
    → préfixe `ARM-` ; `COM-13201` renvoie « Aucun territoire trouvé ». Toute autre
    commune (dont Corse 2A/2B, non numérique) → `COM-`."""
    n = insee.strip()
    if n.isdigit():
        v = int(n)
        if 75101 <= v <= 75120 or 69381 <= v <= 69389 or 13201 <= v <= 13216:
            return f"ARM-{n}"
    return f"COM-{n}"


class InseeMelodiClient:
    """Client INSEE Mélodi (données locales par commune). Sans clé."""

    def __init__(self, timeout: int = TIMEOUT):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "france-opendata", "Accept": "application/json"})

    def _get(self, dataset: str, insee: str, max_result: int = 2000) -> list[dict]:
        params = {"GEO": _geo_ref(insee), "maxResult": max_result}
        last: Exception | None = None
        for backoff in (0.0, *_RETRY_BACKOFF):
            if backoff:
                time.sleep(backoff)
            try:
                resp = self._session.get(f"{BASE_URL}/{dataset}", params=params,
                                         timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e  # reset / timeout amont → flakiness, on retente
                continue
            if resp.status_code >= 500:
                last = requests.HTTPError(f"INSEE Mélodi {resp.status_code} (amont)",
                                          response=resp)
                continue  # 5xx intermittent (#194-195) → retente
            resp.raise_for_status()  # 4xx = requête/territoire invalide → non retryable
            return resp.json().get("observations", [])
        raise last  # toutes les tentatives ont échoué sur une erreur amont transitoire

    @staticmethod
    def _value(obs: dict):
        return (obs.get("measures") or {}).get("OBS_VALUE_NIVEAU", {}).get("value")

    def _pick(self, observations, want: dict, totals_for: Optional[list] = None):
        """Valeur de l'observation dont les dimensions matchent `want`, les autres
        dimensions listées dans `totals_for` devant valoir '_T'."""
        for o in observations:
            dims = o.get("dimensions") or {}
            if any(dims.get(k) != v for k, v in want.items()):
                continue
            if totals_for and any(dims.get(k) not in ("_T", None)
                                  for k in totals_for if k not in want):
                continue
            return self._value(o)
        return None

    @staticmethod
    def _periods(observations) -> list[str]:
        """Millésimes présents dans la réponse, triés (INSEE fait glisser la fenêtre
        RP chaque année : 2011/2016/2022 hier, 2012/2017/2023 aujourd'hui — ne JAMAIS
        figer l'année, la lire des données)."""
        ps = {o.get("dimensions", {}).get("TIME_PERIOD") for o in observations}
        return sorted(p for p in ps if p)

    def population(self, insee: str) -> dict[str, Any]:
        """Population municipale (RP) à tous les millésimes diffusés (typ. 3 : ~n-11,
        n-6, n ; sert la comparaison d'évolution)."""
        obs = self._get("DS_RP_POPULATION_PRINC", insee)
        out: dict[str, int] = {}
        for period in self._periods(obs):
            v = self._pick(obs, {"SEX": "_T", "AGE": "_T", "RP_MEASURE": "POP",
                                 "TIME_PERIOD": period})
            if v is not None:
                out[period] = round(v)
        return out

    def familles(self, insee: str) -> dict[str, Any]:
        """Familles par type (couples sans/avec enfant, monoparentales h/f) + parts %.
        NB : 'familles' ⊂ ménages (exclut personnes seules et ménages sans famille)."""
        obs = self._get("DS_RP_FAMILLE_COMP", insee)
        periods = self._periods(obs)
        latest = periods[-1] if periods else None
        counts: dict[str, int] = {}
        for o in obs:
            d = o.get("dimensions", {})
            if d.get("TIME_PERIOD") == latest and d.get("NCH") == "_T" and d.get("TFN") in _TFN:
                v = self._value(o)
                if v:
                    counts[_TFN[d["TFN"]]] = round(v)
        total = sum(counts.values())
        parts = {k: round(100 * v / total, 1) for k, v in counts.items()} if total else {}
        mono = counts.get("monoparentale_homme", 0) + counts.get("monoparentale_femme", 0)
        return {
            "total_familles": total or None,
            "effectifs": counts,
            "parts_pct": parts,
            "part_monoparentales_pct": round(100 * mono / total, 1) if total else None,
        }

    def personnes_seules(self, insee: str) -> Optional[int]:
        """Nombre de ménages d'une personne (mesure ONEPERS, sommée par tranche d'âge)."""
        obs = self._get("DS_RP_MENAGES_PRINC", insee)
        periods = self._periods(obs)
        latest = periods[-1] if periods else None
        total, found = 0, False
        for o in obs:
            d = o.get("dimensions", {})
            if (d.get("TIME_PERIOD") == latest and d.get("RP_MEASURE") == "ONEPERS"
                    and d.get("NOC") == "P1" and d.get("CIVIL_STATUS") == "_T"
                    and d.get("COUPLE") == "_T" and d.get("OCS") == "DW_MAIN"
                    and d.get("AGE") not in ("_T", None)):
                v = self._value(o)
                if v:
                    total += v
                    found = True
        return round(total) if found else None

    def revenus(self, insee: str) -> dict[str, Any]:
        """Revenus Filosofi : médiane du niveau de vie (€/UC) et taux de pauvreté (%).
        Déciles indisponibles à la maille communale (secret statistique) → null."""
        obs = self._get("DS_FILOSOFI_CC", insee)
        return {
            "millesime": (obs[0]["dimensions"]["TIME_PERIOD"] if obs else None),
            "revenu_median_uc_eur": self._pick(obs, {"FILOSOFI_MEASURE": "MED_SL"}),
            "taux_pauvrete_pct": self._pick(obs, {"FILOSOFI_MEASURE": "PR_MD60"}),
        }

    def logement(self, insee: str) -> dict[str, Any]:
        """Parc de logements (RP, dernier millésime) : principales / vacants /
        secondaires, taux de vacance, et statut d'occupation des résidences
        principales."""
        obs = self._get("DS_RP_LOGEMENT_PRINC", insee)
        periods = self._periods(obs)
        latest = periods[-1] if periods else None
        base_total = ["NRG_SRC", "CARS", "NOR", "BUILD_END", "TDW", "TSH", "CARPARK", "L_STAY"]
        rp = {"RP_MEASURE": "DWELLINGS", "TIME_PERIOD": latest}
        principales = self._pick(obs, {**rp, "OCS": "DW_MAIN"}, totals_for=base_total)
        vacants = self._pick(obs, {**rp, "OCS": "DW_VAC"}, totals_for=base_total)
        secondaires = self._pick(obs, {**rp, "OCS": "DW_SEC_DW_OCC"}, totals_for=base_total)
        total = sum(v for v in (principales, vacants, secondaires) if v)
        return {
            "millesime": latest,
            "residences_principales": round(principales) if principales else None,
            "logements_vacants": round(vacants) if vacants else None,
            "logements_secondaires": round(secondaires) if secondaires else None,
            "taux_vacance_pct": round(100 * vacants / total, 1) if (vacants and total) else None,
            "statut_occupation": self._tenure(obs, latest),
        }

    def _tenure(self, obs, latest: Optional[str]) -> dict[str, Any]:
        tot_dims = ["NRG_SRC", "CARS", "NOR", "BUILD_END", "TDW", "CARPARK", "L_STAY"]
        counts: dict[str, int] = {}
        for code, label in _TSH.items():
            v = self._pick(obs, {"RP_MEASURE": "DWELLINGS", "TIME_PERIOD": latest,
                                 "OCS": "DW_MAIN", "TSH": code}, totals_for=tot_dims)
            if v:
                counts[label] = round(v)
        total = sum(counts.values())
        parts = {k: round(100 * v / total, 1) for k, v in counts.items()} if total else {}
        return {"effectifs": counts, "parts_pct": parts,
                "part_social_pct": parts.get("locataire_social_hlm")}
