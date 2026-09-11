"""Répertoire National des Élus — maires et présidents d'EPCI (open data).

Source : ministère de l'Intérieur, via data.gouv.fr, jeu `repertoire-national-des-elus-1`.
Sans clé, Licence Ouverte. Deux ressources lues ici : `elus-maire-mai.csv` (~4 Mo,
un maire par commune) et `elus-conseiller-communautaire-epci.csv` (~10 Mo, dont les
présidents d'intercommunalité).

**Ce que ça apporte.** Sur une cible publique, le décideur n'est ni un contact
commercial ni un dirigeant SIRENE : c'est un élu. Le maire pour une commune, le
président pour un EPCI. Ce jeu les nomme, avec la date de début de leur mandat — donc
aussi de quoi savoir si l'interlocuteur a changé depuis la dernière campagne.

**Ce que ce client ne rend PAS, volontairement.** Le fichier source porte la DATE DE
NAISSANCE et le sexe de chaque élu. Ces colonnes sont publiques — un mandat électif
l'est — mais elles n'ont aucun usage dans une prospection, et les recopier ne ferait
qu'étendre la surface de données personnelles manipulée. Elles sont écartées à la
lecture, pas filtrées en aval.

⚠️ **Le libellé de commune n'est pas une clé.** « Sainte-Marie » existe des dizaines
de fois : le rapprochement se fait sur le code INSEE, jamais sur le nom.

⚠️ **Un EPCI n'a pas de code INSEE de commune** : il porte un SIREN. Le fichier des
conseillers communautaires donne à la place la « commune de rattachement » (le siège),
rendue ici dans `code_commune` — ce n'est pas la commune de l'élu.

⚠️ **Le CSV est en UTF-8, mais le serveur ne le dit pas.** Il répond `text/csv` sans
charset, et `requests` applique alors ISO-8859-1 : les en-têtes accentués deviennent
« Nom de l'Ã©lu », plus aucune colonne à accent ne matche, et chaque élu sort avec un
nom VIDE — sans la moindre erreur, puisque les colonnes sans accent (« Code de la
commune ») continuent de filtrer. Le décodage est donc forcé, pas négocié.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

DATASET = "repertoire-national-des-elus-1"
API_DATAGOUV = "https://www.data.gouv.fr/api/1/datasets"

# Les colonnes du CSV, telles qu'elles sont écrites dans l'en-tête.
_COL_DEPT = "Code du département"
_COL_COMMUNE = "Code de la commune"
_COL_COMMUNE_LIB = "Libellé de la commune"
_COL_COMMUNE_RATTACH = "Code de la commune de rattachement"
_COL_COMMUNE_RATTACH_LIB = "Libellé de la commune de rattachement"
_COL_NOM = "Nom de l'élu"
_COL_PRENOM = "Prénom de l'élu"
_COL_DEBUT_MANDAT = "Date de début du mandat"
_COL_DEBUT_FONCTION = "Date de début de la fonction"
_COL_SIREN_EPCI = "N° SIREN"
_COL_EPCI_LIB = "Libellé de l'EPCI"
_COL_FONCTION = "Libellé de la fonction"


def _texte(valeur: Any) -> Optional[str]:
    if valeur is None:
        return None
    t = str(valeur).strip()
    return t or None


def _elu(row: dict[str, str], fonction: str) -> dict[str, Any]:
    """Un élu, sans sa date de naissance ni son sexe — voir le module."""
    return {
        "nom": _texte(row.get(_COL_NOM)),
        "prenom": _texte(row.get(_COL_PRENOM)),
        "fonction": _texte(row.get(_COL_FONCTION)) or fonction,
        # Un maire a sa commune ; un président d'EPCI n'a que la commune de
        # rattachement de l'EPCI. Même notion de lieu, deux colonnes selon le fichier.
        "code_commune": _texte(row.get(_COL_COMMUNE)) or _texte(row.get(_COL_COMMUNE_RATTACH)),
        "commune": _texte(row.get(_COL_COMMUNE_LIB)) or _texte(row.get(_COL_COMMUNE_RATTACH_LIB)),
        "code_departement": _texte(row.get(_COL_DEPT)),
        "siren_epci": _texte(row.get(_COL_SIREN_EPCI)),
        "epci": _texte(row.get(_COL_EPCI_LIB)),
        "debut_mandat": _texte(row.get(_COL_DEBUT_MANDAT)),
        "debut_fonction": _texte(row.get(_COL_DEBUT_FONCTION)),
    }


class ElusClient:
    """Maires et présidents d'EPCI. Sans clé.

    Les deux CSV sont résolus via l'API data.gouv (leur URL porte un horodatage qui
    change à chaque publication), téléchargés puis gardés en mémoire par instance.
    """

    def __init__(self, timeout: Any = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()
        self._cache: dict[str, list[dict[str, str]]] = {}

    def _url_ressource(self, motif: str) -> str:
        resp = self.session.get(
            f"{API_DATAGOUV}/{DATASET}/",
            headers={"Accept": "application/json", "User-Agent": "france-opendata"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        for r in resp.json().get("resources", []):
            if motif in (r.get("url") or ""):
                return r["url"]
        raise LookupError(f"ressource introuvable dans le jeu {DATASET} : {motif}")

    def _charger(self, motif: str) -> list[dict[str, str]]:
        if motif in self._cache:
            return self._cache[motif]
        resp = self.session.get(
            self._url_ressource(motif),
            headers={"User-Agent": "france-opendata"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        # `resp.text` appliquerait ISO-8859-1 (pas de charset annoncé) : voir le module.
        rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8")), delimiter=";"))
        self._cache[motif] = rows
        return rows

    def maires(
        self,
        code_commune: Optional[str] = None,
        departement: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Les maires, par commune ou par département.

        Args:
            code_commune: code INSEE de la commune — le seul rapprochement sûr.
            departement: code département.
            limit: élus rendus.
        """
        if not code_commune and not departement:
            raise ValueError("Provide at least one of: code_commune, departement")
        rows = self._charger("elus-maire")
        retenus = [
            _elu(r, "Maire") for r in rows
            if (not code_commune or _texte(r.get(_COL_COMMUNE)) == code_commune)
            and (not departement or _texte(r.get(_COL_DEPT)) == departement)
        ]
        borne = max(1, int(limit))
        return {
            "fonction": "Maire",
            "total": min(len(retenus), borne),
            "tronque": len(retenus) > borne,
            "signaux": retenus[:borne],
        }

    def presidents_epci(
        self,
        siren: Optional[str] = None,
        departement: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Les présidents d'intercommunalité.

        Le fichier liste TOUS les conseillers communautaires : seule la fonction
        « Président » est retenue ici, sinon une requête sur un département rendrait
        des milliers de conseillers sans décideur identifiable.
        """
        rows = self._charger("elus-conseiller-communautaire")
        retenus = []
        for r in rows:
            fonction = (_texte(r.get(_COL_FONCTION)) or "").lower()
            if not fonction.startswith("président"):
                continue
            if siren and _texte(r.get(_COL_SIREN_EPCI)) != siren:
                continue
            if departement and _texte(r.get(_COL_DEPT)) != departement:
                continue
            retenus.append(_elu(r, "Président d'EPCI"))
        borne = max(1, int(limit))
        return {
            "fonction": "Président d'EPCI",
            "total": min(len(retenus), borne),
            "tronque": len(retenus) > borne,
            "signaux": retenus[:borne],
        }
