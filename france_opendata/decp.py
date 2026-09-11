"""DECP — marchés publics ATTRIBUÉS (données essentielles de la commande publique).

Source : ministère de l'Économie, portail data.economie.gouv.fr,
  https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/decp-v3-marches-valides/records
Sans clé, Licence Ouverte. ~700 000 marchés notifiés.

**Ce que ça apporte face au BOAMP.** Le BOAMP publie l'AVIS : un besoin, une date limite.
Les DECP publient l'ISSUE : qui a gagné, pour combien, notifié quand, sur quelle durée.
C'est la seule source qui dit qui remporte les marchés d'un territoire — donc la
concurrence réelle, pas celle qu'on suppose.

⚠️ **Le SIRET du titulaire est stocké en NOMBRE** : son zéro de tête est perdu
(`5780122700059` pour `05780122700059`, mesuré le 11/09/2026). Même défaut que les
SIREN de BEGES, même correction sûre : un SIRET fait quatorze chiffres. Elle ne
s'applique qu'aux identifiants déclarés `SIRET` — un identifiant étranger ou TVA ne
se complète pas.

⚠️ **Le titulaire principal n'a pas de dénomination dans le jeu.** Les colonnes
`titulaire_denominationsociale_2` et `_3` existent, pas `_1` : le nom du lauréat se
résout par son SIRET (SIRENE), ce qui reste à l'appelant. `denomination: None` sur le
titulaire principal n'est donc pas une donnée manquante par accident.

⚠️ **Le portail est servi par OpenDataSoft.** Il répondait depuis un poste le
11/09/2026 ; l'egress depuis la box de production reste à vérifier.
"""
from __future__ import annotations

from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

API = ("https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
       "decp-v3-marches-valides/records")

PAGE_MAX = 100


def normaliser_identifiant(valeur: Any, type_identifiant: Any) -> Optional[str]:
    """Rend un SIRET à quatorze chiffres ; tout autre identifiant tel quel.

    Le complément n'est sûr que pour un SIRET : un numéro de TVA intracommunautaire ou
    un identifiant étranger n'a pas de longueur fixe connue, et le compléter
    fabriquerait un identifiant qui n'existe pas.
    """
    if valeur is None or valeur == "":
        return None
    s = str(valeur).strip().replace(" ", "")
    if str(type_identifiant or "").upper() == "SIRET" and s.isdigit() and len(s) < 14:
        return s.zfill(14)
    return s


def _titulaires(row: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i in (1, 2, 3):
        ident = row.get(f"titulaire_id_{i}")
        if ident in (None, ""):
            continue
        type_id = row.get(f"titulaire_typeidentifiant_{i}")
        out.append({
            "rang": i,
            "identifiant": normaliser_identifiant(ident, type_id),
            "type_identifiant": type_id,
            # absente pour le rang 1 dans le jeu source — voir le module
            "denomination": row.get(f"titulaire_denominationsociale_{i}"),
        })
    return out


def _signal(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref_key": row.get("id") or f"{row.get('acheteur_id')}|{row.get('datenotification')}|{row.get('objet')}",
        "objet": row.get("objet"),
        "nature": row.get("nature"),
        "procedure": row.get("procedure"),
        "code_cpv": row.get("codecpv"),
        "date_notification": row.get("datenotification"),
        "duree_mois": row.get("dureemois"),
        "montant": row.get("montant"),
        "acheteur": {
            "siret": normaliser_identifiant(row.get("acheteur_id"), "SIRET"),
            "nom": row.get("acheteur_nom"),
        },
        "lieu_execution": {
            "code": row.get("lieuexecution_code"),
            "type_code": row.get("lieuexecution_typecode"),
            "nom": row.get("lieuexecution_nom"),
        },
        "titulaires": _titulaires(row),
    }


class DecpClient:
    """Marchés publics attribués. Sans clé."""

    def __init__(self, timeout: Any = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()

    def marches(
        self,
        mot_cle: Optional[str] = None,
        titulaire_siret: Optional[str] = None,
        acheteur_siret: Optional[str] = None,
        lieu: Optional[str] = None,
        depuis: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Marchés notifiés, les plus récents d'abord.

        Args:
            mot_cle: recherche dans l'objet du marché (« photovoltaïque »…).
            titulaire_siret: tous les marchés gagnés par cet établissement.
            acheteur_siret: tous les marchés passés par cet acheteur.
            lieu: début du code de lieu d'exécution — un département (« 59 ») ou un
                code postal. Le type de code varie d'un marché à l'autre.
            depuis: date de notification minimale, `AAAA-MM-JJ`.
            limit: marchés rendus, 1 à 100.
        """
        if not any([mot_cle, titulaire_siret, acheteur_siret, lieu]):
            raise ValueError("Provide at least one of: mot_cle, titulaire_siret, acheteur_siret, lieu")
        clauses = []
        if mot_cle:
            clauses.append(f'search(objet, "{mot_cle}")')
        if titulaire_siret:
            # stocké en nombre : on compare à la valeur SANS zéro de tête
            clauses.append(f"titulaire_id_1 = {int(titulaire_siret)}")
        if acheteur_siret:
            clauses.append(f'acheteur_id = "{acheteur_siret}"')
        if lieu:
            clauses.append(f'startswith(lieuexecution_code, "{lieu}")')
        if depuis:
            clauses.append(f'datenotification >= date"{depuis}"')
        borne = max(1, min(int(limit), PAGE_MAX))
        resp = self.session.get(
            API,
            params={"where": " and ".join(clauses), "limit": borne,
                    "order_by": "datenotification desc"},
            headers={"Accept": "application/json", "User-Agent": "france-opendata"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        signaux = [_signal(r) for r in data.get("results", [])]
        total = data.get("total_count", len(signaux))
        return {"total": total, "rendus": len(signaux), "tronque": total > len(signaux), "signaux": signaux}
