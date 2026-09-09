"""Rapprocher un SITE (une adresse, un point) d'un ÉTABLISSEMENT du répertoire.

Le problème que ça résout : les sources de site — conso électrique par adresse, permis,
ICPE, DPE — rendent une ADRESSE, jamais un SIRET. Les rapprocher au nom de voie marche
mal là où ça compte : en zone industrielle, « BOULEVARD DE L'INDUSTRIE » ne distingue
rien, et une usine dont le siège social est à cinq cents kilomètres n'a aucun mot en
commun avec lui. Mesuré sur Bordeaux Métropole avec un recoupement de mots : 52 % des
sites rapprochés, et le reste majoritairement en zone d'activité.

Ici, le rapprochement se fait par la **géométrie** : un point contre un point. Le stock
SIRENE porte les coordonnées d'établissement en Lambert 93, la BAN géocode l'adresse du
site — `geo.distance_lambert_m` les ramène dans le même plan.

Deux règles portent l'honnêteté du résultat :

1. **Un rayon**, au-delà duquel on ne rapproche pas. Sans lui, le « plus proche » finit
   toujours par exister, même à trois kilomètres.
2. **Un écart au deuxième**. Deux établissements à la même adresse — le cas normal d'une
   zone industrielle ou d'un site multi-exploitants — doivent sortir `ambigu`, pas un
   tirage au sort présenté comme un résultat. C'est cette règle-là qui manquait au
   rapprochement par mots : il tranchait toujours, y compris quand rien ne permettait
   de trancher.

Un établissement sans coordonnées ne devient jamais « loin » : il sort `sans_position`,
compté à part. Une distance inconnue n'est pas une grande distance.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .geo import distance_lambert_m

# Un site industriel s'étend : le point BAN d'une voie et le point INSEE d'un
# établissement peuvent légitimement être à quelques centaines de mètres.
RAYON_DEFAUT_M = 300.0
# En deçà de cet écart entre le premier et le deuxième, on ne tranche pas.
ECART_MIN_M = 150.0


def classer_par_distance(
    lat: float,
    lon: float,
    etablissements: Iterable[dict[str, Any]],
    *,
    x_key: str = "lambert_x",
    y_key: str = "lambert_y",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classe les établissements par distance au point. Rend `(classés, sans_position)`.

    Les classés portent `distance_m`. Ceux sans coordonnées exploitables sortent à part,
    jamais en fin de classement — on ne sait pas où ils sont, ce n'est pas qu'ils sont loin.
    """
    classes, sans = [], []
    for e in etablissements:
        d = distance_lambert_m(lat, lon, e.get(x_key), e.get(y_key))
        if d is None:
            sans.append(e)
        else:
            classes.append({**e, "distance_m": round(d, 1)})
    classes.sort(key=lambda e: e["distance_m"])
    return classes, sans


def rapprocher(
    lat: float,
    lon: float,
    etablissements: Iterable[dict[str, Any]],
    *,
    rayon_m: float = RAYON_DEFAUT_M,
    ecart_min_m: float = ECART_MIN_M,
    x_key: str = "lambert_x",
    y_key: str = "lambert_y",
) -> dict[str, Any]:
    """Rapproche un point d'un établissement, avec un verdict qui dit ce qu'il vaut.

    `statut` ∈ {`résolu`, `ambigu`, `non résolu`} :
    - `résolu` : un seul candidat dans le rayon, ou le premier devance le deuxième d'au
      moins `ecart_min_m` ;
    - `ambigu` : plusieurs candidats dans le rayon, trop serrés pour trancher — le
      `match` est rendu quand même, mais le statut dit de ne pas s'y fier seul ;
    - `non résolu` : aucun candidat dans le rayon.

    Rend aussi `candidats_dans_rayon`, `distance_m`, `ecart_au_second_m` et
    `sans_position` — de quoi comprendre le verdict sans rejouer le calcul.
    """
    classes, sans = classer_par_distance(lat, lon, etablissements, x_key=x_key, y_key=y_key)
    dans = [e for e in classes if e["distance_m"] <= rayon_m]
    base = {
        "candidats_examines": len(classes) + len(sans),
        "candidats_dans_rayon": len(dans),
        "sans_position": len(sans),
        "rayon_m": rayon_m,
    }
    if not dans:
        plus_proche = classes[0]["distance_m"] if classes else None
        return {**base, "statut": "non résolu", "match": None, "distance_m": None,
                "ecart_au_second_m": None, "plus_proche_hors_rayon_m": plus_proche}
    premier = dans[0]
    ecart = round(dans[1]["distance_m"] - premier["distance_m"], 1) if len(dans) > 1 else None
    tranche = ecart is None or ecart >= ecart_min_m
    return {**base,
            "statut": "résolu" if tranche else "ambigu",
            "match": premier,
            "distance_m": premier["distance_m"],
            "ecart_au_second_m": ecart,
            "plus_proche_hors_rayon_m": None}


def combiner(
    geometrique: dict[str, Any],
    id_lexical: Optional[str],
    *,
    id_key: str = "siren",
) -> dict[str, Any]:
    """Croise le rapprochement géométrique avec un second signal (nom de voie, enseigne).

    Mesuré sur Bordeaux Métropole, 75 sites : les mots de voie seuls tranchent 27 % des
    cas, la géométrie seule 31 %, **les deux ensemble 51 %**. Ils ne se trompent pas aux
    mêmes endroits — la voie porte l'information là où le géocodage est grossier (un
    lieu-dit, une zone sans numéro), la géométrie là où le nom ne distingue rien (une
    « rue de l'Industrie » avec quatre exploitants).

    Sur les trente cas où les deux tranchent, ils sont d'accord vingt-neuf fois. Le
    trente-et-unième est un `conflit` — rendu comme tel, jamais arbitré en silence : deux
    méthodes indépendantes qui se contredisent est précisément le signal qu'il faut
    remonter à un humain.

    `confiance` ∈ {`accord`, `signal_unique`, `conflit`, `aucun`}.
    """
    id_geo = (geometrique.get("match") or {}).get(id_key)
    base = {**geometrique}
    if id_geo and id_lexical and id_geo == id_lexical:
        return {**base, "statut": "résolu", "confiance": "accord", id_key: id_geo}
    if id_geo and id_lexical and id_geo != id_lexical:
        return {**base, "statut": "ambigu", "confiance": "conflit",
                id_key: None, "conflit": {"geometrie": id_geo, "lexical": id_lexical}}
    seul = id_geo or id_lexical
    if seul:
        # Un seul signal tranche : c'est utilisable, mais ça ne vaut pas un accord.
        statut = "résolu" if geometrique.get("statut") != "ambigu" or not id_geo else "ambigu"
        return {**base, "statut": statut, "confiance": "signal_unique", id_key: seul}
    return {**base, "statut": geometrique.get("statut", "non résolu"),
            "confiance": "aucun", id_key: None}
