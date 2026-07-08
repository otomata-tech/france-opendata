"""BODACC — publications légales des entreprises françaises (open data DILA).

Dataset: annonces-commerciales on OpenDataSoft v2.1.
No auth required. Licence Ouverte / Etalab 2.0.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

# Valeurs réelles du champ ODS `familleavis` :
#   collective · conciliation · creation · divers · dpc · immatriculation
#   · modification · radiation · retablissement_professionnel · vente
# On accepte quelques alias « parlants » côté appelant et on les mappe sur la
# valeur canonique — l'ancien "procedure_collective" ne matchait AUCUNE ligne.
_FAMILLE_ALIASES = {
    "procedure_collective": "collective",
    "procedures_collectives": "collective",
    "procedure": "collective",
}


def _famille_ods(famille: Optional[str]) -> Optional[str]:
    if not famille:
        return famille
    return _FAMILLE_ALIASES.get(famille, famille)


def _siren_of(registre: Any) -> Optional[str]:
    """Le champ `registre` est une liste type ['791195415', '791 195 415'] :
    le SIREN = l'élément 9 chiffres sans espaces."""
    if isinstance(registre, str):
        registre = [registre]
    for item in registre or []:
        digits = str(item).replace(" ", "")
        if len(digits) == 9 and digits.isdigit():
            return digits
    return None


class BodaccClient:
    BASE_URL = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records"

    # ODS v2.1 : limit max 100 par page ; on borne le nombre de SIREN par
    # requête OR pour garder l'URL sous les limites serveur.
    _PAGE_LIMIT = 100
    _BATCH_CHUNK = 40

    def __init__(self, timeout: tuple[float, float] | float = DEFAULT_TIMEOUT):
        self.timeout = timeout

    def search_by_siren(
        self,
        siren: str,
        famille: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search BODACC announcements for a SIREN.

        Args:
            siren: 9-digit SIREN.
            famille: Filter by family — collective (procédures collectives),
                conciliation, creation, modification, radiation, vente, dpc
                (dépôt des comptes), immatriculation, retablissement_professionnel.
            limit: Max results.
        """
        clauses = [f'registre="{siren}"']
        famille = _famille_ods(famille)
        if famille:
            clauses.append(f'familleavis="{famille}"')

        resp = requests.get(self.BASE_URL, params={
            "where": " AND ".join(clauses),
            "order_by": "dateparution desc",
            "limit": str(min(limit, self._PAGE_LIMIT)),
        }, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return {
            "results": self._clean_results(data.get("results", [])),
            "total_count": data.get("total_count", 0),
        }

    def search_batch(
        self,
        sirens: list[str],
        famille: Optional[str] = None,
        chunk_size: Optional[int] = None,
    ) -> dict[str, Any]:
        """Lookup BODACC announcements for MANY SIRENs in few ODS requests.

        Purement déterministe : on récupère les annonces (champs typés), on les
        remet à plat (`annonces`), et on en dérive des COMPTES (`synthese`).
        Aucune interprétation du texte libre `jugement.texte` (ex. « en procédure
        collective ? ») — ce jugement reste à l'appelant, qui lit `texte`.

        Args:
            sirens: liste de SIREN (9 chiffres, espaces tolérés).
            famille: filtre `familleavis` optionnel (voir `search_by_siren`) —
                ex. "collective" pour ne remonter que les procédures collectives.
            chunk_size: nombre de SIREN par requête OR (défaut 40).

        Returns:
            {
              "annonces": [ {siren, date_parution, date_jugement, famille,
                             type_avis, jugement_famille, jugement_nature,
                             texte, tribunal, commercant, bodacc_id}, … ],
              "synthese": {sirens_interroges, sirens_avec_annonce,
                           sirens_sans_annonce, annonces_total, par_famille,
                           par_type_avis, par_jugement_nature,
                           par_jugement_famille},
            }
        """
        norm = [s for s in (str(x).replace(" ", "") for x in sirens) if s]
        seen: set[str] = set()
        uniq = [s for s in norm if not (s in seen or seen.add(s))]

        famille = _famille_ods(famille)
        step = chunk_size or self._BATCH_CHUNK

        raw: list[dict[str, Any]] = []
        for i in range(0, len(uniq), step):
            raw.extend(self._fetch_chunk(uniq[i:i + step], famille))

        annonces = [self._retape(r) for r in raw]
        annonces.sort(key=lambda a: a.get("date_parution") or "", reverse=True)

        return {
            "annonces": annonces,
            "synthese": self._synthese(uniq, annonces),
        }

    def _fetch_chunk(self, sirens: list[str], famille: Optional[str]) -> list[dict]:
        """Une plage de SIREN, paginée jusqu'à épuisement (total_count > 100)."""
        or_clause = " OR ".join(f'registre="{s}"' for s in sirens)
        where = f"({or_clause})"
        if famille:
            where += f' AND familleavis="{famille}"'

        out: list[dict] = []
        offset = 0
        while True:
            resp = requests.get(self.BASE_URL, params={
                "where": where,
                "order_by": "dateparution desc",
                "limit": str(self._PAGE_LIMIT),
                "offset": str(offset),
            }, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            page = data.get("results", [])
            out.extend(page)
            offset += self._PAGE_LIMIT
            if offset >= data.get("total_count", 0) or not page:
                break
            if offset >= 10000:  # plafond d'offset ODS
                break
        return out

    def search(
        self,
        query: Optional[str] = None,
        departement: Optional[str] = None,
        famille: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search BODACC announcements by keyword / filters."""
        clauses: list[str] = []
        if query:
            clauses.append(f'search(commercant, "{query}")')
        if departement:
            clauses.append(f'numerodepartement="{departement}"')
        famille = _famille_ods(famille)
        if famille:
            clauses.append(f'familleavis="{famille}"')
        if date_from:
            clauses.append(f'dateparution>="{date_from}"')
        if date_to:
            clauses.append(f'dateparution<="{date_to}"')

        params: dict[str, str] = {
            "order_by": "dateparution desc",
            "limit": str(min(limit, self._PAGE_LIMIT)),
        }
        if clauses:
            params["where"] = " AND ".join(clauses)

        resp = requests.get(self.BASE_URL, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return {
            "results": self._clean_results(data.get("results", [])),
            "total_count": data.get("total_count", 0),
        }

    @staticmethod
    def _retape(rec: dict) -> dict[str, Any]:
        """Une annonce brute ODS → ligne plate, table-friendly."""
        import json as _json

        jug = rec.get("jugement")
        if isinstance(jug, str):
            try:
                jug = _json.loads(jug)
            except ValueError:
                jug = {}
        jug = jug or {}
        return {
            "siren": _siren_of(rec.get("registre")),
            "date_parution": rec.get("dateparution"),
            "date_jugement": jug.get("date"),
            "famille": rec.get("familleavis_lib") or rec.get("familleavis"),
            "type_avis": rec.get("typeavis_lib") or rec.get("typeavis"),
            "jugement_famille": jug.get("famille"),
            "jugement_nature": jug.get("nature"),
            "texte": jug.get("complementJugement"),
            "tribunal": rec.get("tribunal"),
            "commercant": rec.get("commercant"),
            "bodacc_id": rec.get("id"),
        }

    @staticmethod
    def _synthese(sirens: list[str], annonces: list[dict]) -> dict[str, Any]:
        """Chiffres d'agrégation déterministes sur des champs typés."""
        avec = {a["siren"] for a in annonces if a.get("siren")}
        return {
            "sirens_interroges": len(sirens),
            "sirens_avec_annonce": len(avec),
            "sirens_sans_annonce": len(sirens) - len(avec),
            "annonces_total": len(annonces),
            "par_famille": dict(Counter(a["famille"] for a in annonces if a.get("famille"))),
            "par_type_avis": dict(Counter(a["type_avis"] for a in annonces if a.get("type_avis"))),
            "par_jugement_nature": dict(Counter(a["jugement_nature"] for a in annonces if a.get("jugement_nature"))),
            "par_jugement_famille": dict(Counter(a["jugement_famille"] for a in annonces if a.get("jugement_famille"))),
        }

    @staticmethod
    def _clean_results(results: list[dict]) -> list[dict]:
        """Keep only non-null fields and parse JSON strings."""
        import json as _json

        cleaned = []
        for r in results:
            out: dict[str, Any] = {}
            for k, v in r.items():
                if v is None:
                    continue
                if isinstance(v, str) and v.startswith("{"):
                    try:
                        v = _json.loads(v)
                    except ValueError:
                        pass
                out[k] = v
            cleaned.append(out)
        return cleaned
