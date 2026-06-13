"""FINESS — annuaire des établissements sanitaires et médico-sociaux (open data).

Source : data.gouv.fr, dataset `finess-extraction-du-fichier-des-etablissements`
(ASIP/ATIH). Pas de clé, Licence Ouverte. Le fichier « géolocalisé »
(`etalab-cs1100507`) a deux types de lignes : `structureet` (l'établissement,
~30 colonnes) et `geolocalisation`. Ce client ne lit que les `structureet`.

Fichier stock volumineux (~46k établissements) : on résout la ressource la plus
récente via l'API data.gouv, on télécharge et on garde en mémoire (cache par
instance). L'enrichissement SIRENE et la logique métier restent à l'appelant.
"""
from __future__ import annotations

import csv
import io
import unicodedata
from typing import Any, Optional

import requests


DATASET = "finess-extraction-du-fichier-des-etablissements"
DATAGOUV_API = "https://www.data.gouv.fr/api/1/datasets"

# Indices de colonnes des lignes `structureet` (format etalab-cs1100507).
_COL = {
    "type": 0, "finess_et": 1, "finess_ej": 2, "rs": 3, "rs_longue": 4,
    "num_voie": 7, "typ_voie": 8, "voie": 9, "commune_code": 12,
    "departement_code": 13, "departement": 14, "cp_commune": 15,
    "tel": 16, "fax": 17, "categorie_code": 18, "categorie": 19,
    "categorie_agregee_code": 20, "categorie_agregee": 21, "siret": 22, "ape": 23,
}


def _normalize(text: str) -> str:
    """Minuscule sans accents — pour la recherche floue."""
    return unicodedata.normalize("NFD", (text or "").lower()).encode("ascii", "ignore").decode()


class FinessClient:
    def __init__(self, timeout: int = 120, csv_url: Optional[str] = None):
        self.timeout = timeout
        self._csv_url = csv_url  # override (fichier local/miroir) ; sinon résolu via data.gouv
        self._rows: Optional[list[dict[str, Any]]] = None

    def latest_csv_url(self) -> str:
        """URL de la ressource FINESS géolocalisée (cs1100507) la plus récente."""
        if self._csv_url:
            return self._csv_url
        resp = requests.get(f"{DATAGOUV_API}/{DATASET}/", timeout=self.timeout)
        resp.raise_for_status()
        cands = [
            r for r in resp.json().get("resources", [])
            if (r.get("format") == "csv") and ("cs1100507" in (r.get("url") or ""))
        ]
        if not cands:
            raise RuntimeError("ressource FINESS cs1100507 introuvable sur data.gouv")
        cands.sort(key=lambda r: r.get("created_at") or r.get("url") or "", reverse=True)
        return cands[0]["url"]

    def _load(self) -> list[dict[str, Any]]:
        if self._rows is not None:
            return self._rows
        raw = requests.get(self.latest_csv_url(), timeout=self.timeout).content.decode("utf-8", "replace")
        reader = csv.reader(io.StringIO(raw), delimiter=";")
        next(reader, None)  # en-tête
        rows: list[dict[str, Any]] = []
        for r in reader:
            if len(r) < 24 or r[_COL["type"]] != "structureet":
                continue  # ignore les lignes geolocalisation / tronquées
            item = {
                "finess_et": r[_COL["finess_et"]],
                "finess_ej": r[_COL["finess_ej"]],
                "rs": r[_COL["rs"]],
                "rs_longue": r[_COL["rs_longue"]],
                "adresse": f"{r[_COL['num_voie']]} {r[_COL['typ_voie']]} {r[_COL['voie']]}".strip(),
                "cp_commune": r[_COL["cp_commune"]],
                "commune_code": r[_COL["commune_code"]],
                "departement_code": r[_COL["departement_code"]],
                "departement": r[_COL["departement"]],
                "tel": r[_COL["tel"]],
                "categorie_code": r[_COL["categorie_code"]],
                "categorie": r[_COL["categorie"]],
                "categorie_agregee": r[_COL["categorie_agregee"]],
                "siret": r[_COL["siret"]],
                "ape": r[_COL["ape"]],
            }
            item["_search"] = _normalize(f"{item['rs']} {item['rs_longue']} {item['cp_commune']}")
            rows.append(item)
        self._rows = rows
        return rows

    def by_code(self, finess: str) -> Optional[dict[str, Any]]:
        """Établissement par code FINESS (ET ou EJ exact), ou None."""
        for it in self._load():
            if it["finess_et"] == finess or it["finess_ej"] == finess:
                return {k: v for k, v in it.items() if k != "_search"}
        return None

    def search(
        self,
        q: str,
        *,
        departement: Optional[str] = None,
        categorie: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Recherche par code FINESS (préfixe) ou nom (multi-mots, sans accents).

        `departement` : code (ex. "75"). `categorie` : sous-chaîne du libellé
        (ex. "EHPAD", "Centre Hospitalier"). Renvoie `{count, results}`.
        """
        data = self._load()
        words = _normalize(q).split()
        is_code = q.isdigit()
        dep = departement.strip() if departement else None
        cat = _normalize(categorie) if categorie else None
        et, ej, named = [], [], []
        for it in data:
            if dep and it["departement_code"] != dep:
                continue
            if cat and cat not in _normalize(it["categorie"]):
                continue
            if is_code:
                if it["finess_et"].startswith(q):
                    et.append(it)
                elif it["finess_ej"].startswith(q):
                    ej.append(it)
            elif all(w in it["_search"] for w in words):
                named.append(it)
        results = [{k: v for k, v in it.items() if k != "_search"} for it in (et + ej + named)[:limit]]
        return {"count": len(results), "results": results}
