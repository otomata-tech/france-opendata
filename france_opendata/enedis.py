"""Enedis — consommation électrique annuelle par adresse (open data).

Source : OpenDataSoft Enedis, dataset `consommation-annuelle-entreprise-par-adresse`.
  https://opendata.enedis.fr/explore/dataset/consommation-annuelle-entreprise-par-adresse/
Pas de clé. Licence Ouverte. Enedis publie N-1 (donc "2024" dispo en 2026).

Renvoie des **signaux bruts** : adresse + conso (MWh/an) + NAF2 + secteur +
nombre de sites. La résolution adresse → SIREN/SIRET et tout scoring métier
restent à la charge de l'appelant (ex. harnais de prospection PV).

Volumétrie typique de la bande 150–6000 MWh, secteur INDUSTRIE :
  dept industriel (59) ~830 lignes · dept urbain (75) ~50-150 lignes.

Gotcha : on tape l'endpoint **export** (`/exports/json`), pas `/records` qui
plafonne à offset=10000 → les grosses partitions (Paris tertiaire) échouent
sinon. Découper par (dept × secteur) côté appelant garde chaque réponse raisonnable.
"""
from __future__ import annotations

from typing import Any, Optional

from .opendatasoft import OpendatasoftClient


PORTAL = "https://opendata.enedis.fr"
DATASET = "consommation-annuelle-entreprise-par-adresse"

# Secteurs Enedis (code_grand_secteur, NAF1 grossier).
SECTEURS = ("INDUSTRIE", "TERTIAIRE", "AGRICULTURE")


def _signal(r: dict[str, Any]) -> Optional[dict[str, Any]]:
    code_commune = r.get("code_commune")
    code_dept = r.get("code_departement")
    adresse = r.get("adresse")
    annee = r.get("annee")
    mwh = r.get("consommation_annuelle_totale_de_ladresse_mwh")
    if not code_commune or not code_dept or not adresse or not annee or mwh is None:
        return None
    return {
        # Clé composite stable pour l'idempotence côté appelant.
        "ref_key": f"{code_commune}|{adresse}|{annee}",
        "annee": annee,
        "code_dept": code_dept,
        "code_commune": code_commune,
        "nom_commune": r.get("nom_commune") or "",
        "adresse": adresse,
        "numero_voie": r.get("numero_de_voie"),
        "libelle_voie": r.get("libelle_de_voie"),
        "naf2": r.get("code_secteur_naf2"),
        "secteur": r.get("code_grand_secteur") or "INDUSTRIE",
        "categorie_conso": r.get("code_categorie_consommation"),
        "nombre_de_sites": r.get("nombre_de_sites") or 0,
        "mwh": mwh,
        "raw": r,
    }


class EnedisClient:
    def __init__(self, timeout: int = 120):
        self.timeout = timeout
        self.ods = OpendatasoftClient(PORTAL, timeout=timeout)

    def consommation_par_adresse(
        self,
        annee: str,
        *,
        dept: Optional[str] = None,
        secteur: Optional[str] = None,
        min_mwh: Optional[float] = None,
        max_mwh: Optional[float] = None,
        limit: int = -1,
    ) -> dict[str, Any]:
        """Signaux de conso de la bande (année + dept? + secteur? + min/max MWh).

        `annee` : année de référence (ex. "2024"). `dept` : code INSEE 2-3 chars
        (None = France entière — gros volume). `secteur` ∈ SECTEURS. `min_mwh` :
        borne basse (150 = filtre métier PV courant). `max_mwh` : ne PAS capper en
        général (les très gros consommateurs sont les meilleures cibles PV).
        Retourne `{"total": int, "signals": [ {ref_key, mwh, adresse, …}, … ]}`.
        """
        parts = [f'annee="{annee}"']
        if dept:
            parts.append(f'code_departement="{dept}"')
        if secteur:
            parts.append(f'code_grand_secteur="{secteur}"')
        if min_mwh is not None:
            parts.append(f"consommation_annuelle_totale_de_ladresse_mwh>={min_mwh}")
        if max_mwh is not None:
            parts.append(f"consommation_annuelle_totale_de_ladresse_mwh<={max_mwh}")
        where = " AND ".join(parts)
        rows = self.ods.export(DATASET, "json", where=where, limit=limit)
        signals = [s for r in rows if (s := _signal(r))]
        return {"total": len(signals), "signals": signals}
