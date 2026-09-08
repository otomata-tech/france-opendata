"""BEGES — bilans d'émissions de gaz à effet de serre déclarés (ADEME, open data).

Source : API DataFair de l'ADEME, dataset `9nd9avrbto3l14md-wkode4o` (« Bilan GES »).
  https://data.ademe.fr/data-fair/api/v1/datasets/9nd9avrbto3l14md-wkode4o/lines
Sans clé. Licence Ouverte. ~11 800 bilans, dont ~7 000 d'organisations **obligées**
(art. L229-25 du code de l'environnement : entreprises de plus de 500 salariés,
collectivités de plus de 50 000 habitants, État).

**Ce que ça apporte que les réseaux ne donnent pas.** Les jeux de consommation
électrique (`enedis`, `odre`) sont indexés par ADRESSE ou par IRIS : ils décrivent un
SITE, et il faut le résoudre pour retrouver l'entreprise. Ici la clé est le **SIREN** :
le bilan se joint directement à l'organisation. C'est donc la source qui couvre ce
qu'aucun réseau ne localise — et elle porte l'énergie déclarée, pas seulement mesurée
au compteur.

⚠️ **Le SIREN est stocké en NOMBRE, donc son zéro de tête est perdu.** 150 lignes du
jeu portent une valeur à huit chiffres ou moins : HEXAOM y est `95720314` pour
`095720314`, Fnac Darty `55800296` pour `055800296`. Une jointure sur `str(valeur)`
les rate toutes, sans erreur. `_signal` re-complète à neuf chiffres — c'est la seule
correction que ce module se permet, et elle est sûre : un SIREN fait neuf chiffres.

⚠️ **Un poste d'émission absent n'est pas un zéro.** Les 22 postes `p11`…`p61` ne sont
pas tous renseignés par tous les déclarants : sommer en traitant `null` comme 0 rend un
total plus bas que la réalité, et indétectable. Les totaux par catégorie ne somment que
ce qui est déclaré, et `postes_declares` / `postes_absents` disent sur quoi ils portent.

⚠️ **Une année de reporting n'est pas l'année de publication** : un bilan publié en 2026
peut porter sur 2015. `annee_de_reporting` est le champ qui compte pour comparer.
"""
from __future__ import annotations

from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

API_BASE = "https://data.ademe.fr/data-fair/api/v1/datasets/9nd9avrbto3l14md-wkode4o"

# Les 22 postes du dispositif, groupés par catégorie d'émissions (méthode BEGES v4/v5).
CATEGORIES: dict[str, tuple[str, ...]] = {
    "1_directes": ("p11", "p12", "p13", "p14", "p15"),
    "2_indirectes_energie": ("p21", "p22"),
    "3_transport": ("p31", "p32", "p33", "p34", "p35"),
    "4_produits_achetes": ("p41", "p42", "p43", "p44", "p45"),
    "5_produits_vendus": ("p51", "p52", "p53", "p54"),
    "6_autres": ("p61",),
}

_SELECT = ",".join([
    "raison_sociale", "siren_principal", "siret", "apenaf_associe", "libelle",
    "annee_de_reporting", "date_de_publication", "structure_obligee",
    "type_de_structure", "mode_de_consolidation", "region", "code_departement",
    "nombre_de_salariesdagents_de_lensemble_des_siren_declares_sur_ce_bilan_lors_de_lannee_de_reporting_du_bilan",
    "lien_url_vers_le_rapport_complet_du_beges",
    *(f"emissions_publication_{p}" for cat in CATEGORIES.values() for p in cat),
])


def normaliser_siren(valeur: Any) -> Optional[str]:
    """Rend un SIREN à neuf chiffres, ou None.

    Le jeu stocke le SIREN en nombre : `95720314` est en réalité `095720314`. Un
    zéro-padding à neuf est sûr — un SIREN ne fait jamais plus. Au-delà de neuf
    chiffres on ne devine pas : la valeur est rendue telle quelle et l'appelant voit
    qu'elle ne fait pas la bonne longueur.
    """
    if valeur is None or valeur == "":
        return None
    s = str(valeur).strip().replace(" ", "")
    if not s.isdigit():
        return None
    return s.zfill(9) if len(s) <= 9 else s


def _emissions(row: dict[str, Any]) -> dict[str, Any]:
    """Totaux par catégorie, en ne sommant QUE les postes déclarés."""
    out: dict[str, Any] = {}
    declares = absents = 0
    for cat, postes in CATEGORIES.items():
        vals = [row.get(f"emissions_publication_{p}") for p in postes]
        presents = [v for v in vals if isinstance(v, (int, float))]
        declares += len(presents)
        absents += len(vals) - len(presents)
        out[cat] = {
            "total": round(sum(presents), 3) if presents else None,
            "postes_declares": len(presents),
            "postes_absents": len(vals) - len(presents),
        }
    totaux = [c["total"] for c in out.values() if c["total"] is not None]
    return {
        "par_categorie": out,
        # Somme des catégories renseignées. `None` si rien n'est déclaré — pas zéro.
        "total": round(sum(totaux), 3) if totaux else None,
        "postes_declares": declares,
        "postes_absents": absents,
        "unite": "tCO2e",
    }


def _signal(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    siren = normaliser_siren(row.get("siren_principal"))
    annee = row.get("annee_de_reporting")
    if not siren or not annee:
        return None
    salaries = row.get(
        "nombre_de_salariesdagents_de_lensemble_des_siren_declares_sur_ce_bilan_"
        "lors_de_lannee_de_reporting_du_bilan"
    )
    return {
        # Un SIREN peut déclarer plusieurs années : la clé porte les deux.
        "ref_key": f"{siren}|{annee}",
        "siren": siren,
        "siret": row.get("siret"),
        "raison_sociale": row.get("raison_sociale") or "",
        "naf": row.get("apenaf_associe"),
        "libelle_naf": row.get("libelle"),
        "annee_reporting": annee,
        "date_publication": row.get("date_de_publication"),
        "obligee": bool(row.get("structure_obligee")),
        "type_structure": row.get("type_de_structure"),
        "consolidation": row.get("mode_de_consolidation"),
        "region": row.get("region"),
        "code_departement": row.get("code_departement"),
        "tranche_salaries": salaries,
        "rapport_url": row.get("lien_url_vers_le_rapport_complet_du_beges"),
        "emissions": _emissions(row),
        "raw": row,
    }


class BegesClient:
    """Bilans GES déclarés, joignables par SIREN. Sans clé."""

    def __init__(self, timeout: Any = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()

    def _lines(self, qs: Optional[str], size: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"size": min(max(size, 1), 10000), "select": _SELECT}
        if qs:
            params["qs"] = qs
        r = self.session.get(f"{API_BASE}/lines", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("results", []) or []

    def bilans(
        self,
        *,
        siren: Optional[str] = None,
        naf: Optional[str] = None,
        annee: Optional[int] = None,
        departement: Optional[str] = None,
        obligee: Optional[bool] = None,
        size: int = 100,
    ) -> dict[str, Any]:
        """Bilans déclarés, filtrés. Rend `{"total", "bilans": [...]}`.

        `siren` : neuf chiffres — le zéro de tête est géré des deux côtés. `naf` : un
        code (`"8610Z"`) ou un préfixe de division (`"86"`), qui vaut alors pour toutes
        ses sous-classes. `annee` porte sur l'ANNÉE DE REPORTING, pas de publication.
        `obligee=True` ne garde que les organisations soumises à l'obligation légale.
        """
        clauses = []
        if siren:
            s = normaliser_siren(siren)
            # Le champ est numérique : on interroge la valeur SANS son zéro de tête.
            clauses.append(f"siren_principal:{int(s)}" if s else "siren_principal:-1")
        if naf:
            n = naf.replace(".", "").upper()
            clauses.append(f"apenaf_associe:{n}" if len(n) >= 5 else f"apenaf_associe:{n}*")
        if annee:
            clauses.append(f"annee_de_reporting:{int(annee)}")
        if departement:
            clauses.append(f'code_departement:"{departement}"')
        if obligee is not None:
            clauses.append(f"structure_obligee:{str(bool(obligee)).lower()}")
        qs = " AND ".join(clauses) if clauses else None
        rows = self._lines(qs, size)
        bilans = [s for r in rows if (s := _signal(r))]
        bilans.sort(key=lambda b: (b["annee_reporting"] or 0), reverse=True)
        return {"total": len(bilans), "bilans": bilans}

    def par_siren(self, siren: str, *, size: int = 20) -> dict[str, Any]:
        """Tous les bilans déclarés par un SIREN, du plus récent au plus ancien."""
        return self.bilans(siren=siren, size=size)
