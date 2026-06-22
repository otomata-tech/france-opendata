"""Géorisques — installations classées (ICPE) du registre national.

Source : API publique Géorisques (Ministère de la Transition écologique / BRGM),
`https://www.georisques.gouv.fr/api/v1/installations_classees`, sans clé.

Use case prospection : détecter les GROS SITES INDUSTRIELS quand la consommation
électrique est masquée dans l'open-data Enedis (secret statistique — consommateur
unique dominant sur son adresse). La fiche ICPE donne le régime (Déclaration /
Enregistrement / Autorisation), le statut IED (les sites industriels les plus
lourds), Seveso, l'état d'activité, la géolocalisation, le service d'inspection
(DREAL) et les rapports d'inspection — de quoi fonder une présomption « gros
consommateur » SOURCÉE (code AIOT), jamais une mesure de conso.

NB : l'API ne renvoie pas la consommation énergétique — aucun open-data ne la
donne pour ces sites. C'est un détecteur de magnitude industrielle, pas un
compteur.
"""
from __future__ import annotations

from typing import Any, Optional

import requests

BASE_URL = "https://www.georisques.gouv.fr/api/v1"
TIMEOUT = 30


class GeorisquesClient:
    """Client installations classées (ICPE) Géorisques. Sans clé."""

    def installations_classees(
        self,
        siret: Optional[str] = None,
        code_insee: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Recherche d'installations classées par SIRET ou commune INSEE.

        Args:
            siret: SIRET de l'établissement (14 chiffres) — match exact.
            code_insee: code commune INSEE (5 caractères) — toutes les ICPE
                de la commune.
            page: page 1-based.
            page_size: taille de page (max 100 côté API).

        Returns:
            {"results": int, "page": int, "total_pages": int, "data": [fiche...]}
            Chaque fiche : raisonSociale, adresse, codeInsee/commune, codeNaf,
            longitude/latitude, regime, ied (bool), statutSeveso, etatActivite,
            codeAIOT, siret, serviceAIOT (DREAL), inspections (date + URL PDF),
            flags filières (industrie, carriere, eolienne, bovins...).
        """
        if not siret and not code_insee:
            raise ValueError("Provide at least one of: siret, code_insee")
        params: dict[str, Any] = {"page": page, "page_size": min(page_size, 100)}
        if siret:
            params["siret"] = siret
        if code_insee:
            params["code_insee"] = code_insee

        resp = requests.get(
            f"{BASE_URL}/installations_classees",
            params=params,
            headers={"Accept": "application/json", "User-Agent": "france-opendata"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, params: dict) -> dict[str, Any]:
        resp = requests.get(
            f"{BASE_URL}/{path}",
            params=params,
            headers={"Accept": "application/json", "User-Agent": "france-opendata"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def risques_commune(self, code_insee: str) -> dict[str, Any]:
        """Risques naturels et technologiques recensés sur une commune (GASPAR).

        Args:
            code_insee: code commune INSEE (5 caractères).

        Returns:
            {"code_insee": str, "risques": [libellés longs distincts]} — inondation,
            mouvement de terrain, retrait-gonflement des argiles, sismicité, TMD,
            ICPE/Seveso, etc. Liste vide si aucun risque recensé.
        """
        data = self._get("gaspar/risques",
                         {"code_insee": code_insee, "page": 1, "page_size": 20})
        libelles: list[str] = []
        for row in data.get("data") or []:
            for d in row.get("risques_detail", []):
                lib = d.get("libelle_risque_long")
                if lib and lib not in libelles:
                    libelles.append(lib)
        return {"code_insee": code_insee, "risques": libelles}

    def alea_argiles(self, lon: float, lat: float) -> dict[str, Any]:
        """Aléa retrait-gonflement des argiles (RGA) au point (lon, lat).

        Returns {"code_exposition", "exposition"} — niveau d'exposition (faible /
        moyen / fort) au point. L'argile fort est un facteur de coût fondations.
        """
        data = self._get("rga", {"latlon": f"{lon},{lat}"})
        return {"code_exposition": data.get("codeExposition"),
                "exposition": data.get("exposition")}
