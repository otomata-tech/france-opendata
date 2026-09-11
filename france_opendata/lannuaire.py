"""Annuaire de l'administration — services publics et leurs responsables (DILA).

Source : API de l'annuaire Service-Public (DILA),
  https://api-lannuaire.service-public.fr/api/explore/v2.1/catalog/datasets/api-lannuaire-administration/records
Sans clé, Licence Ouverte. ~36 000 mairies et des milliers d'autres services — préfectures,
DDFIP, directions départementales, établissements publics.

**Ce que ça apporte.** Sur une cible publique, l'enrichissement payant est mal armé : il
cherche des dirigeants d'entreprise. Cet annuaire donne le standard, le courriel
générique, le site, et — c'est le point — `affectation_personne` : le RESPONSABLE nommé
du service, avec sa fonction et, souvent, son courriel direct.

⚠️ **Plusieurs champs sont des chaînes JSON, pas des objets.** `pivot`, `telephone`,
`site_internet` et `affectation_personne` arrivent sérialisés : lus tels quels, ils
sortent comme du texte que personne ne sait exploiter. Ils sont décodés ici, et une
valeur qui ne se décode pas reste signalée plutôt que devinée.

⚠️ **Le portail est servi par OpenDataSoft**, sur son propre domaine. Les domaines
`*.opendatasoft.com` bloquent les IP de datacenter ; celui-ci répond depuis la box de
production (vérifié le 11/09/2026). S'il se met à refuser, c'est la première piste.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

API = ("https://api-lannuaire.service-public.fr/api/explore/v2.1/catalog/datasets/"
       "api-lannuaire-administration/records")

# Plafond de l'API Explore v2.1 par requête.
PAGE_MAX = 100


def decoder_json(valeur: Any) -> tuple[Any, bool]:
    """Rend `(valeur_decodee, illisible)`.

    Une chaîne qui ne se décode pas n'est pas rendue comme vide : `illisible=True` le
    dit, pour qu'une donnée corrompue à la source ne passe pas pour une donnée absente.
    """
    if valeur is None or valeur == "":
        return None, False
    if not isinstance(valeur, str):
        return valeur, False
    try:
        return json.loads(valeur), False
    except (ValueError, TypeError):
        return None, True


def _valeurs(liste: Any) -> list[str]:
    """`[{"valeur": "02 38…", "description": ""}]` → `["02 38…"]`."""
    if not isinstance(liste, list):
        return []
    return [str(x["valeur"]).strip() for x in liste if isinstance(x, dict) and x.get("valeur")]


def _responsables(liste: Any) -> list[dict[str, Any]]:
    """Les personnes affectées au service, avec leur fonction.

    Le courriel d'une personne a la forme du téléphone — une liste de
    `{libelle, valeur}` — alors que celui du service est une chaîne.
    """
    if not isinstance(liste, list):
        return []
    out = []
    for a in liste:
        if not isinstance(a, dict):
            continue
        p = a.get("personne") or {}
        out.append({
            "civilite": p.get("civilite"),
            "prenom": p.get("prenom"),
            "nom": p.get("nom"),
            "fonction": a.get("fonction"),
            "grade": p.get("grade"),
            "courriel": _valeurs(p.get("adresse_courriel")),
            "telephone": _valeurs(a.get("telephone")),
        })
    return out


def _signal(row: dict[str, Any]) -> dict[str, Any]:
    illisibles = []
    decodes = {}
    for champ in ("pivot", "telephone", "site_internet", "affectation_personne"):
        val, ko = decoder_json(row.get(champ))
        decodes[champ] = val
        if ko:
            illisibles.append(champ)
    pivot = decodes["pivot"] if isinstance(decodes["pivot"], list) else []
    return {
        "ref_key": row.get("id") or f"{row.get('siret')}|{row.get('nom')}",
        "nom": row.get("nom"),
        "type_service": [p.get("type_service_local") for p in pivot if isinstance(p, dict)],
        "siren": row.get("siren"),
        "siret": row.get("siret"),
        "code_commune": row.get("code_insee_commune"),
        "telephone": _valeurs(decodes["telephone"]),
        "courriel": row.get("adresse_courriel"),
        "site_internet": _valeurs(decodes["site_internet"]),
        "responsables": _responsables(decodes["affectation_personne"]),
        "champs_illisibles": illisibles,
    }


class LannuaireClient:
    """Services publics et leurs responsables nommés. Sans clé."""

    def __init__(self, timeout: Any = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()

    def services(
        self,
        siren: Optional[str] = None,
        code_commune: Optional[str] = None,
        type_service: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Services de l'annuaire, par SIREN ou par commune.

        Args:
            siren: SIREN de l'organisme — une commune, un établissement public.
            code_commune: code INSEE — tous les services implantés.
            type_service: type « pivot » (`mairie`, `prefecture`, `dd_fip`…).
            limit: services rendus, 1 à 100.
        """
        if not siren and not code_commune:
            raise ValueError("Provide at least one of: siren, code_commune")
        clauses = []
        if siren:
            clauses.append(f'siren="{siren}"')
        if code_commune:
            clauses.append(f'code_insee_commune="{code_commune}"')
        if type_service:
            clauses.append(f'pivot like "{type_service}"')
        borne = max(1, min(int(limit), PAGE_MAX))
        resp = self.session.get(
            API,
            params={"where": " and ".join(clauses), "limit": borne},
            headers={"Accept": "application/json", "User-Agent": "france-opendata"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        signaux = [_signal(r) for r in data.get("results", [])]
        total = data.get("total_count", len(signaux))
        return {
            "total": total,
            "rendus": len(signaux),
            "tronque": total > len(signaux),
            "signaux": signaux,
        }
