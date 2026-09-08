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

⚠️ Enedis publie aussi des lignes **SANS adresse** : de la consommation réelle mais non
localisable (25 lignes et 10 621 MWh sur Bordeaux Métropole). Elles ne peuvent pas
devenir des sites, et les regrouper sous une clé vide en fabriquerait un qui n'existe
pas — elles sont donc COMPTÉES à côté (`lignes_ignorees`, `mwh_ignores`).

⚠️ **Une adresse porte PLUSIEURS lignes — une par division NAF** (`code_secteur_naf2`).
C'est le piège central de ce jeu, et il ne fait pas d'erreur : il rend un résultat
plausible et faux. Deux conséquences, par ordre de gravité :

1. **Seuiller ligne à ligne rate des sites.** Un site dont chaque division est sous le
   seuil mais dont le total le dépasse n'apparaît jamais. Mesuré le 08/09/2026 sur
   Bordeaux Métropole : 79 lignes au-dessus de 2 GWh, mais **75 sites** une fois agrégés,
   dont 2 qu'aucune division seule ne signalait.
2. **Tout comptage au-dessus d'un seuil est un comptage de LIGNES**, pas de sites.

`ref_key` (commune|adresse|année) est donc une clé de SITE, pas de ligne : deux lignes
de la même adresse la partagent. Le CHU de Bordeaux, `33318|1 AVENUE DE MAGELLAN|2024`,
sort en 20 663 et 5 678 MWh — le site en consomme 26 342 et aucune des deux ne le dit.
Un appelant qui dédoublonne dessus perd de la consommation **sans aucune erreur**.
Utiliser `row_key` (qui porte le NAF2) pour l'unicité de ligne, et
`sites_par_adresse()` quand ce qu'on veut est un site.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Union

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
    naf2 = r.get("code_secteur_naf2")
    return {
        # Clé de SITE : deux divisions NAF à la même adresse la PARTAGENT (cf. module).
        "ref_key": f"{code_commune}|{adresse}|{annee}",
        # Clé de LIGNE, unique : c'est celle-ci qu'on dédoublonne.
        "row_key": f"{code_commune}|{adresse}|{annee}|{naf2 or ''}",
        "annee": annee,
        "code_dept": code_dept,
        "code_commune": code_commune,
        "nom_commune": r.get("nom_commune") or "",
        "adresse": adresse,
        "numero_voie": r.get("numero_de_voie"),
        "libelle_voie": r.get("libelle_de_voie"),
        "naf2": naf2,
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
        naf2: Optional[Union[str, Iterable[str]]] = None,
        code_commune: Optional[Union[str, Iterable[str]]] = None,
        code_epci: Optional[str] = None,
        min_mwh: Optional[float] = None,
        max_mwh: Optional[float] = None,
        limit: int = -1,
    ) -> dict[str, Any]:
        """Signaux de conso de la bande (année + dept? + secteur? + min/max MWh).

        `annee` : année de référence (ex. "2024"). `dept` : code INSEE 2-3 chars
        (None = France entière — gros volume). `secteur` ∈ SECTEURS. `min_mwh` :
        borne basse (150 = filtre métier PV courant). `max_mwh` : ne PAS capper en
        général (les très gros consommateurs sont les meilleures cibles PV).

        `naf2` : division(s) NAF à deux chiffres — la maille dans laquelle Enedis publie,
        et la seule qui permette de viser un secteur précis. `secteur` ne connaît que
        trois valeurs grossières : un hôpital et une tour de bureaux y sont tous deux
        « TERTIAIRE ». `code_commune` : un code INSEE ou une liste. `code_epci` : un EPCI
        entier (une métropole, par exemple).

        ⚠️ Rend des LIGNES, une par (adresse × division NAF) : `min_mwh` s'y applique
        ligne à ligne, ce qui rate les sites dont le total seul dépasse le seuil. Pour
        raisonner en sites, voir `sites_par_adresse()`.

        Retourne `{"total": int, "signals": [ {ref_key, row_key, mwh, adresse, …}, … ]}`.
        """
        parts = [f'annee="{annee}"']
        if dept:
            parts.append(f'code_departement="{dept}"')
        if secteur:
            parts.append(f'code_grand_secteur="{secteur}"')
        if naf2:
            divisions = [naf2] if isinstance(naf2, str) else list(naf2)
            if divisions:
                inner = ", ".join(f'"{d}"' for d in divisions)
                parts.append(f"code_secteur_naf2 in ({inner})")
        if code_commune:
            communes = [code_commune] if isinstance(code_commune, str) else list(code_commune)
            if communes:
                inner = ", ".join(f'"{c}"' for c in communes)
                parts.append(f"code_commune in ({inner})")
        if code_epci:
            parts.append(f'code_epci="{code_epci}"')
        if min_mwh is not None:
            parts.append(f"consommation_annuelle_totale_de_ladresse_mwh>={min_mwh}")
        if max_mwh is not None:
            parts.append(f"consommation_annuelle_totale_de_ladresse_mwh<={max_mwh}")
        where = " AND ".join(parts)
        rows = self.ods.export(DATASET, "json", where=where, limit=limit)
        # `limit` borne le PULL, et l'export sort dans l'ordre du jeu (par code commune),
        # pas par consommation : une coupe y perd des lignes arbitraires, pas les plus
        # petites. Mesuré sur la Gironde au défaut de 200 — 20 lignes perdues, dont la
        # deuxième plus grosse du département à 41 979 MWh. On ne peut pas trier au
        # serveur sur cet endpoint : on le DIT, pour qu'une liste tronquée ne se lise
        # jamais comme une liste complète.
        tronque = limit is not None and limit >= 0 and len(rows) >= limit
        signals = []
        ignorees, mwh_ignores = 0, 0.0
        for r in rows:
            sig = _signal(r)
            if sig is None:
                # Enedis publie des lignes SANS adresse : de la consommation réelle, mais
                # non localisable. On ne peut pas la servir comme un site — on la COMPTE.
                ignorees += 1
                mwh_ignores += r.get("consommation_annuelle_totale_de_ladresse_mwh") or 0.0
                continue
            signals.append(sig)
        return {
            "total": len(signals),
            "signals": signals,
            "tronque": tronque,
            "avertissement_troncature": (
                "la coupe suit l'ordre du jeu (code commune), pas la consommation : "
                "remonter `min_mwh` plutôt que baisser `limit`, ou passer limit=-1"
            ) if tronque else None,
            # Additif : ce que le filtre a ramené mais qu'on ne peut pas localiser.
            "lignes_ignorees": ignorees,
            "mwh_ignores": round(mwh_ignores, 3),
            "motif_ignorees": "ligne sans adresse exploitable" if ignorees else None,
        }

    def sites_par_adresse(
        self,
        annee: str,
        *,
        dept: Optional[str] = None,
        code_commune: Optional[Union[str, Iterable[str]]] = None,
        code_epci: Optional[str] = None,
        naf2: Optional[Union[str, Iterable[str]]] = None,
        secteur: Optional[str] = None,
        min_mwh: Optional[float] = None,
        limit: int = -1,
    ) -> dict[str, Any]:
        """Sites (adresses) et leur consommation TOTALE, divisions NAF sommées.

        La maille que presque tout le monde veut sans le savoir. `consommation_par_adresse`
        rend les lignes telles qu'Enedis les publie — une par (adresse × division NAF) ;
        celle-ci les regroupe par (code_commune, adresse) et somme.

        ⚠️ **`min_mwh` s'applique APRÈS la somme, et c'est tout l'intérêt.** Le seuil ne
        peut donc pas être poussé au serveur : on rapatrie le périmètre entier puis on
        agrège. C'est pourquoi un périmètre est OBLIGATOIRE ici (`dept`, `code_commune`
        ou `code_epci`) alors qu'il est optionnel sur les lignes — sans lui, l'appel
        rapatrierait la France.

        Chaque site porte `naf2_principal` (la division qui pèse le plus, celle qui sert à
        la résolution vers SIRENE) et `naf2_detail` (la ventilation complète) : une adresse
        multi-divisions se voit, elle ne se devine pas.

        Retourne `{"total", "lignes_lues", "signals": [{site_key, mwh, naf2_detail, …}]}`.
        """
        if not (dept or code_commune or code_epci):
            raise ValueError(
                "sites_par_adresse exige un périmètre (dept, code_commune ou code_epci) : "
                "le seuil s'applique après agrégation, donc les lignes ne peuvent pas être "
                "filtrées au serveur et un appel national rapatrierait tout le jeu."
            )
        brut = self.consommation_par_adresse(
            annee, dept=dept, code_commune=code_commune, code_epci=code_epci,
            naf2=naf2, secteur=secteur, limit=limit,
        )
        out = agreger_par_adresse(brut["signals"], min_mwh=min_mwh)
        # Une conso non localisable n'est PAS un site : elle ne devient jamais une ligne
        # de résultat, mais elle est rendue à côté pour que le total du périmètre se
        # rapproche sans avoir à retourner à la source.
        out["lignes_ignorees"] = brut["lignes_ignorees"]
        out["mwh_ignores"] = brut["mwh_ignores"]
        out["motif_ignorees"] = brut["motif_ignorees"]
        return out


def agreger_par_adresse(
    signals: Iterable[dict[str, Any]],
    *,
    min_mwh: Optional[float] = None,
) -> dict[str, Any]:
    """Regroupe des lignes `_signal` par (code_commune, adresse) et somme les MWh.

    Fonction pure — séparée du client pour être testable sans réseau, et réutilisable sur
    des lignes déjà en main.
    """
    groupes: dict[tuple[str, str], dict[str, Any]] = {}
    lues = 0
    for s in signals:
        lues += 1
        cle = (s.get("code_commune") or "", s.get("adresse") or "")
        g = groupes.get(cle)
        if g is None:
            g = groupes[cle] = {
                "site_key": s.get("ref_key"),
                "annee": s.get("annee"),
                "code_commune": cle[0],
                "nom_commune": s.get("nom_commune") or "",
                "code_dept": s.get("code_dept") or "",
                "adresse": cle[1],
                "numero_voie": s.get("numero_voie"),
                "libelle_voie": s.get("libelle_voie"),
                "mwh": 0.0,
                "nombre_de_sites": 0,
                "lignes": 0,
                "naf2_detail": {},
                "secteurs": set(),
                "maille": "site",
                "reseau": "distribution",
                "code_iris": None,
                "_mwh_max": -1.0,
            }
        mwh = s.get("mwh") or 0.0
        # L'IRIS n'est pas garanti identique entre deux lignes d'une même adresse : on
        # garde celui de la ligne la PLUS LOURDE, jamais une moyenne qui n'existe pas.
        # Comparé AVANT l'accumulation, sinon la ligne courante bat toujours le total.
        if mwh >= g["_mwh_max"]:
            g["_mwh_max"] = mwh
            g["code_iris"] = (s.get("raw") or {}).get("code_iris")
        g["mwh"] += mwh
        g["nombre_de_sites"] += s.get("nombre_de_sites") or 0
        g["lignes"] += 1
        naf2 = s.get("naf2")
        if naf2:
            g["naf2_detail"][naf2] = g["naf2_detail"].get(naf2, 0.0) + mwh
        if s.get("secteur"):
            g["secteurs"].add(s["secteur"])

    out = []
    for g in groupes.values():
        if min_mwh is not None and g["mwh"] < min_mwh:
            continue
        g["mwh"] = round(g["mwh"], 3)
        g["naf2_detail"] = {k: round(v, 3) for k, v in
                            sorted(g["naf2_detail"].items(), key=lambda kv: -kv[1])}
        g["naf2_principal"] = next(iter(g["naf2_detail"]), None)
        g["multi_naf2"] = len(g["naf2_detail"]) > 1
        g["secteurs"] = sorted(g.pop("secteurs"))   # un set ne se sérialise pas en JSON
        g.pop("_mwh_max", None)
        out.append(g)
    out.sort(key=lambda s: s["mwh"], reverse=True)
    return {"total": len(out), "lignes_lues": lues, "signals": out}
