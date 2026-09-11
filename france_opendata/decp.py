"""DECP — marchés publics ATTRIBUÉS (données essentielles de la commande publique).

Source : ministère de l'Économie, portail data.economie.gouv.fr, deux jeux —
  https://data.economie.gouv.fr/explore/dataset/decp-2022-marches-valides/
  https://data.economie.gouv.fr/explore/dataset/decp-v3-marches-valides/
Sans clé, Licence Ouverte.

**Ce que ça apporte face au BOAMP.** Le BOAMP publie l'AVIS : un besoin, une date limite.
Les DECP publient l'ISSUE : qui a gagné, pour combien, notifié quand, sur quelle durée.
C'est la seule source qui dit qui remporte les marchés d'un territoire — donc la
concurrence réelle, pas celle qu'on suppose.

⚠️ **Deux jeux, un par régime, découpés à la notification.** Les marchés notifiés
depuis le 1er janvier 2024 relèvent de l'arrêté du 22/12/2022 (`decp-2022-…`, ~600 000
marchés, mis à jour chaque jour) ; les précédents, de l'arrêté du 22/03/2019
(`decp-v3-…` — « v3 » est la version du jeu, pas du format : il s'ARRÊTE au 8 février
2024). Ne lire que ce dernier, c'était ne servir aucun marché récent — mesuré le
11/09/2026. Chaque marché est lu dans le jeu du régime en vigueur à sa notification :
le jeu 2022 republie aussi ~99 000 marchés antérieurs, et la moitié d'un échantillon
se retrouvait dans l'ancien jeu sous un autre identifiant — les fusionner compterait
deux fois un même marché. Chaque marché rendu porte son `arrete`.

⚠️ **Le format 2022 ne publie AUCUN nom** — ni acheteur, ni lieu, ni titulaire : tout
se résout par SIRET (SIRENE). Le format 2019 nomme l'acheteur, le lieu, et les
co-titulaires de rang 2 et 3 — jamais le titulaire principal. `nom: None` ou
`denomination: None` n'est donc pas une donnée manquante par accident.

⚠️ **Dans le jeu 2019, le SIRET du titulaire principal est stocké en NOMBRE** : son zéro
de tête est perdu (`5780122700059` pour `05780122700059`). Même correction sûre que
pour les SIREN de BEGES : un SIRET fait quatorze chiffres. Elle ne s'applique qu'aux
identifiants déclarés `SIRET` — un identifiant étranger ou TVA ne se complète pas.

Le portail est servi par OpenDataSoft ; il répond depuis la box de production
(vérifié le 11/09/2026).
"""
from __future__ import annotations

import re
from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

CATALOGUE = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets"

# Entrée en vigueur de l'arrêté du 22/12/2022 : la notification décide du jeu.
BASCULE = "2024-01-01"

# (arrêté, jeu, clause qui borne le jeu à son régime, titulaire principal en nombre ?)
JEUX = (
    ("2022", "decp-2022-marches-valides", f"datenotification >= date'{BASCULE}'", False),
    ("2019", "decp-v3-marches-valides", f"datenotification < date'{BASCULE}'", True),
)

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


def clause_titulaire(siret: str, principal_en_nombre: bool) -> str:
    """Titulaire à n'importe quel rang — un co-titulaire de groupement a gagné aussi.

    Le rang 1 du jeu 2019 est un entier : comparé à une chaîne, il ne matcherait rien.
    """
    s = str(siret).strip()
    rang1 = f"titulaire_id_1 = {int(s)}" if principal_en_nombre else f'titulaire_id_1 = "{s}"'
    return f'({rang1} or titulaire_id_2 = "{s}" or titulaire_id_3 = "{s}")'


# Le jeu 2022 remplit les rangs sans co-titulaire par « CDL », en identifiant ET en
# type (648 963 rangs 2 sur 702 092, le 11/09/2026) — là où le jeu 2019 laisse vide.
# Ce n'est pas une donnée : rendu tel quel, il ferait un co-traitant fantôme.
_SANS_TITULAIRE = "CDL"


def _titulaires(row: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i in (1, 2, 3):
        ident = row.get(f"titulaire_id_{i}")
        type_id = row.get(f"titulaire_typeidentifiant_{i}")
        if ident in (None, "") or (ident == _SANS_TITULAIRE and type_id == _SANS_TITULAIRE):
            continue
        out.append({
            "rang": i,
            "identifiant": normaliser_identifiant(ident, type_id),
            "type_identifiant": type_id,
            # jamais publiée au format 2022, ni pour le rang 1 au format 2019
            "denomination": row.get(f"titulaire_denominationsociale_{i}"),
        })
    return out


def _signal(row: dict[str, Any], arrete: str) -> dict[str, Any]:
    return {
        "ref_key": row.get("id") or f"{row.get('acheteur_id')}|{row.get('datenotification')}|{row.get('objet')}",
        "arrete": arrete,
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

    def _jeu(self, jeu: str, clauses: list[str], borne: int) -> dict[str, Any]:
        resp = self.session.get(
            f"{CATALOGUE}/{jeu}/records",
            params={"where": " and ".join(clauses), "limit": borne,
                    "order_by": "datenotification desc"},
            headers={"Accept": "application/json", "User-Agent": "france-opendata"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def marches(
        self,
        mot_cle: Optional[str] = None,
        titulaire_siret: Optional[str] = None,
        acheteur_siret: Optional[str] = None,
        lieu: Optional[str] = None,
        depuis: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Marchés notifiés, les plus récents d'abord, tous régimes confondus.

        Args:
            mot_cle: recherche dans l'objet du marché (« photovoltaïque »…).
            titulaire_siret: tous les marchés gagnés par cet établissement, seul ou
                en groupement.
            acheteur_siret: tous les marchés passés par cet acheteur.
            lieu: début du code de lieu d'exécution — un département (« 59 ») ou un
                code postal. Le type de code varie d'un marché à l'autre.
            depuis: date de notification minimale, `AAAA-MM-JJ`. À partir de 2024,
                seul le jeu 2022 est interrogé.
            limit: marchés rendus, 1 à 100.
        """
        if not any([mot_cle, titulaire_siret, acheteur_siret, lieu]):
            raise ValueError("Provide at least one of: mot_cle, titulaire_siret, acheteur_siret, lieu")
        if depuis and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", depuis):
            raise ValueError(f"depuis attend une date AAAA-MM-JJ, reçu {depuis!r}")
        borne = max(1, min(int(limit), PAGE_MAX))
        communes = []
        if mot_cle:
            communes.append(f'search(objet, "{mot_cle}")')
        if acheteur_siret:
            communes.append(f'acheteur_id = "{acheteur_siret}"')
        if lieu:
            communes.append(f'startswith(lieuexecution_code, "{lieu}")')
        if depuis:
            communes.append(f"datenotification >= date'{depuis}'")

        total, signaux = 0, []
        for arrete, jeu, regime, principal_en_nombre in JEUX:
            if arrete == "2019" and depuis and depuis >= BASCULE:
                continue  # rien de notifié après la bascule dans ce jeu
            clauses = [regime, *communes]
            if titulaire_siret:
                clauses.append(clause_titulaire(titulaire_siret, principal_en_nombre))
            data = self._jeu(jeu, clauses, borne)
            total += data.get("total_count", 0)
            signaux += [_signal(r, arrete) for r in data.get("results", [])]

        signaux.sort(key=lambda s: s["date_notification"] or "", reverse=True)
        signaux = signaux[:borne]
        return {"total": total, "rendus": len(signaux), "tronque": total > len(signaux), "signaux": signaux}
