"""DPE tertiaire — diagnostics de performance énergétique des bâtiments non résidentiels.

Source : API DataFair de l'ADEME, dataset `j9ol0fwjqckyf49vr29nknbu` (depuis juillet
2021, ~560 000 diagnostics). Sans clé, Licence Ouverte.
  https://data.ademe.fr/data-fair/api/v1/datasets/j9ol0fwjqckyf49vr29nknbu/lines

Complète `dpe.py`, qui ne couvre que le **logement**. Le tertiaire est l'autre moitié du
parc : hôpitaux, enseignement, bureaux, commerces, restauration — et c'est exactement le
périmètre que les données de consommation électrique décrivent sans le qualifier. Enedis
dit *combien* un site consomme ; ici on sait *quoi* est ce bâtiment, sa surface, son
étiquette, son secteur d'activité au sens ERP.

**Le champ qui fait la valeur du croisement : les coordonnées sont déjà en Lambert 93.**
`coordonnee_cartographique_x_ban` / `_y_ban` sont dans la projection du stock SIRENE —
vérifié à 0,0 m contre la reprojection du `_geopoint`. Une ligne de ce jeu se rapproche
donc d'un établissement avec `resolution.rapprocher(..., x_key=…, y_key=…)`, sans
géocodage intermédiaire ni erreur ajoutée.

`id_rnb` porte l'identifiant du Référentiel National des Bâtiments : c'est la clé de
jointure vers la base nationale des bâtiments, un lien exact plutôt que spatial. Il est
absent d'une partie des lignes — l'absence est rendue telle quelle, jamais devinée.

⚠️ Un DPE non géocodé porte `statut_geocodage` qui le DIT. Les lignes sans position
sortent avec `lambert_x`/`lambert_y` à None : on ne les place pas au centre de la
commune, ce qui les ferait rapprocher de n'importe quoi.

⚠️ L'étiquette peut être absente (diagnostic incomplet, modèle ancien) : `None` n'est pas
un G. Le filtre `etiquette` ne rend alors que ce qui en porte une, et ne prétend rien
sur le reste.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Union

import requests

from ._http import DEFAULT_TIMEOUT

API_BASE = "https://data.ademe.fr/data-fair/api/v1/datasets/j9ol0fwjqckyf49vr29nknbu"

_SELECT = ",".join([
    "numero_dpe", "date_etablissement_dpe", "date_fin_validite_dpe",
    "secteur_activite", "categorie_erp", "etiquette_dpe", "etiquette_ges",
    "surface_shon", "surface_utile", "nombre_occupant", "annee_construction",
    "periode_construction", "adresse_ban", "nom_commune_ban", "code_postal_ban",
    "code_insee_ban", "code_departement_ban", "identifiant_ban", "statut_geocodage",
    "coordonnee_cartographique_x_ban", "coordonnee_cartographique_y_ban",
    "id_rnb", "_geopoint",
])


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _signal(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    num = row.get("numero_dpe")
    if not num:
        return None
    x = _num(row.get("coordonnee_cartographique_x_ban"))
    y = _num(row.get("coordonnee_cartographique_y_ban"))
    return {
        "ref_key": str(num),
        "numero_dpe": str(num),
        "date_etablissement": row.get("date_etablissement_dpe"),
        "date_fin_validite": row.get("date_fin_validite_dpe"),
        "secteur_activite": row.get("secteur_activite"),
        "categorie_erp": row.get("categorie_erp"),
        "etiquette_dpe": row.get("etiquette_dpe"),
        "etiquette_ges": row.get("etiquette_ges"),
        "surface_shon": _num(row.get("surface_shon")),
        "surface_utile": _num(row.get("surface_utile")),
        "occupants": row.get("nombre_occupant"),
        "annee_construction": row.get("annee_construction"),
        "adresse": row.get("adresse_ban"),
        "nom_commune": row.get("nom_commune_ban"),
        "code_commune": row.get("code_insee_ban"),
        "code_postal": row.get("code_postal_ban"),
        "code_dept": row.get("code_departement_ban"),
        # Déjà en Lambert 93 : mêmes clés que le stock SIRENE, donc `resolution`
        # les consomme sans conversion ni géocodage.
        "lambert_x": x,
        "lambert_y": y,
        "geocode": row.get("statut_geocodage"),
        "id_rnb": row.get("id_rnb"),
        "raw": row,
    }


class DpeTertiaireClient:
    """DPE des bâtiments tertiaires (ADEME DataFair). Sans clé."""

    def __init__(self, timeout: Any = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()

    def diagnostics(
        self,
        *,
        code_commune: Optional[Union[str, Iterable[str]]] = None,
        departement: Optional[str] = None,
        secteur: Optional[str] = None,
        etiquette: Optional[Union[str, Iterable[str]]] = None,
        surface_min: Optional[float] = None,
        size: int = 100,
    ) -> dict[str, Any]:
        """Diagnostics filtrés. Rend `{"total", "sans_position", "diagnostics": [...]}`.

        `secteur` est une recherche libre sur le libellé ERP (`"hospital"`,
        `"enseignement"`, `"bureaux"`). `etiquette` prend une lettre ou une liste.
        `surface_min` porte sur la SHON.

        `sans_position` compte les diagnostics non géocodés : ils sont rendus, mais
        avec `lambert_x`/`lambert_y` à None, et ne peuvent donc pas être rapprochés
        d'un établissement. Un DPE qu'on ne sait pas placer n'est pas un DPE ailleurs.
        """
        clauses = []
        if code_commune:
            codes = [code_commune] if isinstance(code_commune, str) else list(code_commune)
            if codes:
                clauses.append("(" + " OR ".join(f"code_insee_ban:{c}" for c in codes) + ")")
        if departement:
            clauses.append(f'code_departement_ban:"{departement}"')
        if secteur:
            clauses.append(f'secteur_activite:"{secteur}"')
        if etiquette:
            lettres = [etiquette] if isinstance(etiquette, str) else list(etiquette)
            if lettres:
                clauses.append("(" + " OR ".join(f"etiquette_dpe:{l.upper()}" for l in lettres) + ")")
        if surface_min is not None:
            clauses.append(f"surface_shon:[{surface_min} TO *]")
        params: dict[str, Any] = {"size": min(max(size, 1), 10000), "select": _SELECT}
        if clauses:
            params["qs"] = " AND ".join(clauses)
        r = self.session.get(f"{API_BASE}/lines", params=params, timeout=self.timeout)
        r.raise_for_status()
        rows = r.json().get("results", []) or []
        diags = [s for d in rows if (s := _signal(d))]
        diags.sort(key=lambda d: d["surface_shon"] or 0, reverse=True)
        return {
            "total": len(diags),
            "sans_position": sum(1 for d in diags if d["lambert_x"] is None),
            "diagnostics": diags,
        }
