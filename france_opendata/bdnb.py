"""BDNB — bâtiments et leurs propriétaires personnes morales (CSTB, open data).

Source : API publique de la Base de Données Nationale des Bâtiments,
  https://api.bdnb.io/v1/bdnb/donnees/batiment_groupe_complet_proprietaire
Sans clé. Jeu de données : https://www.data.gouv.fr/datasets/base-de-donnees-nationale-des-batiments

**Ce que ça apporte que rien d'autre ne donne.** Toutes nos sources de lieu rendent
une adresse, une parcelle ou un IRIS — jamais un propriétaire. Le cadastre IGN est la
couche géométrique, DVF donne des transactions sans les parties, et les Fichiers
Fonciers ne sont pas ouverts. Ici la relation est directe et EXACTE : un groupe de
bâtiments porte le SIREN de son propriétaire, quand celui-ci est une personne morale.
C'est l'ancrage qui manquait pour remonter d'un site à une entreprise sans passer par
un rapprochement d'adresse.

Le même enregistrement porte aussi l'emprise au sol, l'usage, l'année de construction,
la classe DPE tertiaire et les consommations professionnelles d'électricité et de gaz
du bâtiment (données locales de l'énergie) — de quoi qualifier un site sans second appel.

⚠️ **Un bâtiment ABSENT n'est pas un bâtiment sans propriétaire.** La table ne contient
que les propriétaires **personnes morales diffusés dans MAJIC** : les personnes
physiques — SCI en nom propre, exploitants agricoles, artisans — sont anonymisées à la
source par la DGFiP et n'y figurent pas du tout. L'absence dit « non diffusable », pas
« inconnu », et surtout pas « sans propriétaire ». `couverture_partielle` le rappelle
sur chaque réponse : un balayage de commune ne rend jamais tout le bâti.

⚠️ **L'API publique plafonne à 10 lignes par requête** (mesuré le 11/09/2026 :
`limit=100` rend 10, l'en-tête `content-range` ne donne pas de total). La pagination
par `offset` fonctionne, donc `limit` au-delà de 10 déclenche plusieurs requêtes — ce
que `requetes` compte, pour que l'appelant sache ce qu'il a payé en latence.

⚠️ **Les consommations sont en kWh/an**, pas en MWh : les champs le disent dans leur
nom (`conso_pro_elec_kwh`), et rien n'est converti ici.

⚠️ **Ne pas filtrer sur `l_siren`** (la liste des copropriétaires) : le champ n'est pas
indexé et la requête meurt en `statement timeout` côté serveur. Le filtre `siren`, lui,
porte le propriétaire principal et répond en quelques dizaines de millisecondes.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

API_BASE = "https://api.bdnb.io/v1/bdnb/donnees"
TABLE = "batiment_groupe_complet_proprietaire"

# Plafond DUR de l'API publique : demander plus ne rend pas plus.
PAGE_MAX = 10

# Garde-fou de notre côté : au-delà, on parle de dizaines de requêtes séquentielles.
LIMIT_MAX = 500

_SELECT = ",".join([
    "batiment_groupe_id",
    "code_commune_insee", "libelle_commune_insee", "code_departement_insee",
    "libelle_adr_principale_ban",
    "siren", "l_siren", "l_denomination_proprietaire", "nb_locaux_open", "dans_majic_pm",
    "surface_emprise_sol", "hauteur_mean", "annee_construction",
    "usage_principal_bdnb_open", "usage_niveau_1_txt",
    "conso_pro_dle_elec_2020", "conso_pro_dle_gaz_2020",
    "classe_conso_energie_dpe_tertiaire", "classe_bilan_dpe",
])


def _nombre(valeur: Any) -> Optional[float]:
    """Rend un nombre, ou None — jamais zéro par défaut."""
    return valeur if isinstance(valeur, (int, float)) else None


def _autres_proprietaires(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Les copropriétaires, hors le principal déjà rendu à part.

    `l_siren` et `l_denomination_proprietaire` sont deux listes parallèles. Quand
    leurs longueurs divergent, on ne réaligne pas à l'aveugle : la dénomination
    manquante sort à None plutôt que d'être prise sur la ligne d'à côté.
    """
    sirens = row.get("l_siren") or []
    noms = row.get("l_denomination_proprietaire") or []
    principal = row.get("siren")
    autres = []
    for i, siren in enumerate(sirens):
        if siren == principal:
            continue
        autres.append({
            "siren": siren,
            "denomination": noms[i] if i < len(noms) else None,
        })
    return autres


def _signal(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Un groupe de bâtiments, son propriétaire et de quoi le qualifier."""
    bid = row.get("batiment_groupe_id")
    if not bid:
        return None
    noms = row.get("l_denomination_proprietaire") or []
    siren = row.get("siren")
    return {
        "ref_key": bid,
        "batiment_groupe_id": bid,
        "code_commune": row.get("code_commune_insee"),
        "nom_commune": row.get("libelle_commune_insee"),
        "code_departement": row.get("code_departement_insee"),
        "adresse": row.get("libelle_adr_principale_ban"),
        "proprietaire": {
            "siren": siren,
            "denomination": noms[0] if noms else None,
            "nb_locaux": row.get("nb_locaux_open"),
            # Toujours vrai dans cette table, rendu quand même : c'est ce qui
            # explique qu'un bâtiment voisin n'y soit pas.
            "dans_majic_pm": row.get("dans_majic_pm"),
        } if siren else None,
        "autres_proprietaires": _autres_proprietaires(row),
        "bati": {
            "emprise_m2": _nombre(row.get("surface_emprise_sol")),
            "hauteur_m": _nombre(row.get("hauteur_mean")),
            "annee_construction": row.get("annee_construction"),
            "usage": row.get("usage_principal_bdnb_open"),
            "usage_niveau_1": row.get("usage_niveau_1_txt"),
        },
        "energie": {
            "conso_pro_elec_kwh": _nombre(row.get("conso_pro_dle_elec_2020")),
            "conso_pro_gaz_kwh": _nombre(row.get("conso_pro_dle_gaz_2020")),
            "millesime_conso": 2020,
            "classe_dpe_tertiaire": row.get("classe_conso_energie_dpe_tertiaire"),
            "classe_dpe": row.get("classe_bilan_dpe"),
        },
        "raw": row,
    }


def seuil_emprise(emprise_min: float) -> int:
    """Seuil d'emprise au format de la colonne source : un entier, arrondi au-dessus."""
    return math.ceil(float(emprise_min))


class BdnbClient:
    """Bâtiments et propriétaires personnes morales. Sans clé."""

    def __init__(self, timeout: Any = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()

    def _page(self, params: dict[str, Any], offset: int) -> list[dict[str, Any]]:
        resp = self.session.get(
            f"{API_BASE}/{TABLE}",
            params={**params, "select": _SELECT, "limit": PAGE_MAX, "offset": offset},
            headers={"Accept": "application/json", "User-Agent": "france-opendata"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows if isinstance(rows, list) else []

    def batiments(
        self,
        code_commune: Optional[str] = None,
        siren: Optional[str] = None,
        batiment_groupe_id: Optional[str] = None,
        departement: Optional[str] = None,
        emprise_min: Optional[float] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Bâtiments et leur propriétaire personne morale.

        Args:
            code_commune: code INSEE — le bâti professionnel d'une commune.
            siren: tous les bâtiments possédés par cette entreprise (indexé, rapide).
            batiment_groupe_id: un groupe précis (`bdnb-bg-…`).
            departement: code département INSEE.
            emprise_min: surface au sol minimale en m² — le filtre de prospection.
            limit: bâtiments rendus, 1 à 500. L'API en sert 10 par requête : au-delà,
                autant d'allers-retours, comptés dans `requetes`.

        Returns:
            `{"total", "requetes", "tronque", "couverture_partielle", "signaux": [...]}`.
            `total` est le nombre RENDU, jamais le nombre existant : l'API ne publie
            aucun compte, et `tronque` dit qu'on s'est arrêté sur `limit`.
        """
        if not any([code_commune, siren, batiment_groupe_id, departement]):
            raise ValueError(
                "Provide at least one of: code_commune, siren, batiment_groupe_id, departement"
            )
        params: dict[str, Any] = {}
        if code_commune:
            params["code_commune_insee"] = f"eq.{code_commune}"
        if siren:
            params["siren"] = f"eq.{siren}"
        if batiment_groupe_id:
            params["batiment_groupe_id"] = f"eq.{batiment_groupe_id}"
        if departement:
            params["code_departement_insee"] = f"eq.{departement}"
        if emprise_min is not None:
            # La colonne est ENTIÈRE : PostgREST rejette `gte.1000.0` en 400 (« invalid
            # input syntax for type integer »). Or tout appelant typé — un corps
            # Pydantic `Optional[float]` — envoie un flottant. Arrondi au-dessus, la
            # sémantique tient : « ≥ 1000,5 m² » vaut « ≥ 1001 » sur des entiers.
            params["surface_emprise_sol"] = f"gte.{seuil_emprise(emprise_min)}"

        borne = max(1, min(int(limit), LIMIT_MAX))
        signaux: list[dict[str, Any]] = []
        requetes = 0
        offset = 0
        while len(signaux) < borne:
            rows = self._page(params, offset)
            requetes += 1
            if not rows:
                break
            signaux.extend(s for s in map(_signal, rows) if s)
            if len(rows) < PAGE_MAX:
                break
            offset += PAGE_MAX

        tronque = len(signaux) > borne
        return {
            "total": min(len(signaux), borne),
            "requetes": requetes,
            "tronque": tronque,
            # Rappel systématique : cette table ne voit QUE les personnes morales.
            "couverture_partielle": (
                "Seuls les propriétaires personnes morales diffusés dans MAJIC figurent "
                "ici. Les personnes physiques (SCI en nom propre, exploitants agricoles, "
                "artisans) sont anonymisées par la DGFiP et absentes : un bâtiment qui "
                "manque n'est pas un bâtiment sans propriétaire."
            ),
            "signaux": signaux[:borne],
        }
