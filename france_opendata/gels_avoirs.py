"""Registre national des gels d'avoirs (DG Trésor) — lecture, validation, contrôle d'un nom.

Le registre recense les personnes, entités et navires visés par une mesure de gel en
vigueur en France (ONU, UE, national ; article L562-4 du code monétaire et financier).
Toute partie à un acte se contrôle contre lui (LCB-FT). Il se publie en un seul fichier
JSON (~13 Mo, ~7 400 inscrits au 16/10/2026), téléchargé par `gels_avoirs_ingest`.

Ce module est PUR (aucune I/O) : valider un registre lu, le ramener à des lignes,
chercher un nom, juger si une copie gardée est encore utilisable. Le stockage est au
consommateur (le service FOD le garde en PostgreSQL).

**Un contrôle ne rassure jamais à tort.** Un registre vide, tronqué ou d'une autre forme
ferait conclure « aucune correspondance » : `valider` lève `RegistreInvalide`, et
`verifier_fraicheur` lève sur une copie trop ancienne. L'appelant rend une erreur, pas
une réponse vide.

**Le prénom ordonne, il n'écarte jamais.** Toutes les correspondances sur le nom sont
rendues ; chacune dit si elle porte aussi le prénom (`sur` = `nom_et_prenom`) ou le nom
seul (`nom_seul` : graphie différente du prénom, entité sans prénom…), celles qui
portent le prénom en tête. Une vigilance ne masque jamais une correspondance.

**Les lettres latines que la décomposition Unicode ne réduit pas** (ł, ø, đ, ß, æ, œ,
ı, ə…) sont translittérées par une table explicite (`_TRANSLITTERATION`) : sans elle,
« Łukasz » ne se trouve pas en tapant « Lukasz ».
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Optional

from .erreurs import ParametreInvalide

URL = "https://gels-avoirs.dgtresor.gouv.fr/ApiPublic/api/v1/publication/derniere-publication-fichier-json"
PAGE = "https://gels-avoirs.dgtresor.gouv.fr/"
PRODUCTEUR = "Direction générale du Trésor"

#: Le registre compte quelques milliers d'inscrits (7 394 au 16/10/2026) : en dessous de
#: ce seuil, il est tronqué.
INSCRITS_MIN = 1_000
#: Période de relecture de la source attendue d'un consommateur qui garde une copie.
GARDE = timedelta(hours=6)
#: Une copie que la source n'a pas confirmée depuis `2 × GARDE` ne sert que si sa
#: publication a moins de sept jours ; au-delà, le contrôle est impossible.
PUBLICATION_MAX = timedelta(days=7)


class RegistreInvalide(ValueError):
    """Le registre ne permet pas de conclure : vide, tronqué, mal formé ou trop ancien."""


def valider(lu: Any) -> dict:
    """Un registre plausible, ou `RegistreInvalide`."""
    pubs = lu.get("Publications") if isinstance(lu, dict) else None
    inscrits = pubs.get("PublicationDetail") if isinstance(pubs, dict) else None
    if not isinstance(pubs, dict) or not pubs.get("DatePublication") or not isinstance(inscrits, list):
        raise RegistreInvalide("registre des gels d'avoirs mal formé (Publications absent)")
    if len(inscrits) < INSCRITS_MIN:
        raise RegistreInvalide(
            f"registre des gels d'avoirs tronqué : {len(inscrits)} inscrits, au moins {INSCRITS_MIN} attendus")
    if any(not isinstance(i, dict) or not isinstance(i.get("Nom"), str)
           or not isinstance(i.get("RegistreDetail"), list) for i in inscrits):
        raise RegistreInvalide("registre des gels d'avoirs mal formé (inscrit sans Nom ou RegistreDetail)")
    return lu


def publication(registre: dict) -> datetime:
    """La date de publication du registre (horodatage avec fuseau).

    La source écrit sept décimales de seconde (`11:06:48.0287882+02:00`), que
    `fromisoformat` refuse avant Python 3.11 : ramenées à six."""
    brut = re.sub(r"(\.\d{6})\d+", r"\1", registre["Publications"]["DatePublication"])
    return datetime.fromisoformat(brut)


def _valeurs(inscrit: dict, type_champ: str, cle: str) -> list[str]:
    return [str(v.get(cle)) for c in inscrit["RegistreDetail"] if c.get("TypeChamp") == type_champ
            for v in (c.get("Valeur") or []) if v.get(cle) not in (None, "")]


def _naissances(inscrit: dict) -> list[str]:
    out = []
    for c in inscrit["RegistreDetail"]:
        if c.get("TypeChamp") != "DATE_DE_NAISSANCE":
            continue
        for v in c.get("Valeur") or []:
            date = "/".join(str(v[k]) for k in ("Jour", "Mois", "Annee") if v.get(k))
            if date:
                out.append(date)
    return out


def inscrits(registre: dict) -> list[dict]:
    """Les inscrits, ramenés à ce qu'un contrôle compare et rend.

    Une ligne : `id_registre`, `nature` (Personne physique / Personne morale / Navire),
    `nom`, `alias`, `prenoms`, `naissances` (`J/M/AAAA`), `fondements` (libellés).
    """
    return [{
        "id_registre": i.get("IdRegistre"),
        "nature": i.get("Nature"),
        "nom": i["Nom"],
        "alias": _valeurs(i, "ALIAS", "Alias"),
        "prenoms": _valeurs(i, "PRENOM", "Prenom"),
        "naissances": _naissances(i),
        "fondements": _valeurs(i, "FONDEMENT_JURIDIQUE", "FondementJuridiqueLabel"),
    } for i in valider(registre)["Publications"]["PublicationDetail"]]


#: Lettres latines sans décomposition Unicode : NFKD + retrait des marques les laisse
#: telles quelles, et `[^a-z0-9]` les effacerait — « Łukasz » deviendrait « ukasz ».
#: Clés en minuscules (appliquée après `lower()`). `ə` (azéri) se romanise « a »
#: (Əliyev → Aliyev) ; il figure dans le registre, comme `ı`, `œ` et `ĸ` (16/10/2026).
_TRANSLITTERATION = str.maketrans({
    "ł": "l", "ø": "o", "đ": "d", "ð": "d", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i",
    "ə": "a", "þ": "th", "ħ": "h", "ŀ": "l", "ŧ": "t", "ĸ": "k", "ŋ": "n", "ſ": "s",
    "ƒ": "f", "ŉ": "n", "ɨ": "i", "ʉ": "u", "ɛ": "e", "ɔ": "o",
})


def normaliser(texte: str) -> str:
    """En minuscules, sans accents ni ponctuation, lettres latines translittérées : la
    comparaison d'un nom écrit de plusieurs façons. Un alias en cyrillique ou en arabe
    s'y réduit à rien."""
    sans_marques = "".join(c for c in unicodedata.normalize("NFKD", texte)
                           if not unicodedata.category(c).startswith("M"))
    latin = sans_marques.lower().translate(_TRANSLITTERATION)
    return re.sub(r"[^a-z0-9]+", " ", latin).strip()


def correspondances(lignes: list[dict], nom: str, prenom: Optional[str] = None) -> list[dict]:
    """Les inscrits dont le nom (ou un alias) porte tous les mots cherchés, et dont un
    prénom contient `prenom` s'il est donné."""
    mots = normaliser(nom).split()
    if not mots:
        # Un nom fait de seule ponctuation ne chercherait rien — et rapprocherait tous
        # les alias non latins, réduits à rien eux aussi.
        raise ParametreInvalide("nom", "au moins une lettre ou un chiffre")
    prenom_n = normaliser(prenom) if prenom is not None else None
    if prenom is not None and not prenom_n:
        raise ParametreInvalide("prenom", "au moins une lettre ou un chiffre")
    out = []
    for ligne in lignes:
        noms = [normaliser(n).split() for n in [ligne["nom"], *ligne["alias"]]]
        if not any(all(m in candidat for m in mots) for candidat in noms):
            continue
        if prenom_n and not any(prenom_n in normaliser(p) for p in ligne["prenoms"]):
            continue
        out.append(ligne)
    return out


def fiche(ligne: dict, sur: str) -> dict:
    """Ce qu'un contrôle rend d'un inscrit : de quoi comparer avant de conclure, et sur
    quoi il correspond (`nom_et_prenom` ou `nom_seul`)."""
    return {
        "sur": sur,
        "id_registre": ligne["id_registre"],
        "nature": ligne["nature"],
        "nom": ligne["nom"],
        "prenoms": ", ".join(ligne["prenoms"]) or None,
        "naissance": ", ".join(ligne["naissances"]) or None,
        "fondement": " ; ".join(ligne["fondements"]) or None,
    }


def controler(lignes: list[dict], nom: str, prenom: Optional[str] = None,
              limit: int = 10) -> dict:
    """Le contrôle d'un nom (et d'un prénom) contre les lignes d'un registre validé.

    Renvoie `{total, nom_et_prenom, nom_seul, correspondances}`. TOUTES les
    correspondances sur le nom comptent dans `total` ; `nom_et_prenom` et `nom_seul` les
    décomptent (sans prénom demandé, toutes sont `nom_seul`). `correspondances` porte
    les `limit` premières fiches, celles qui portent aussi le prénom en tête, chacune
    marquée par `sur`. `limit` borne la liste, jamais les comptes.
    """
    sur_le_nom = correspondances(lignes, nom)
    avec_prenom = correspondances(sur_le_nom, nom, prenom) if prenom is not None else []
    ids = {id(t) for t in avec_prenom}
    fiches = ([fiche(t, "nom_et_prenom") for t in avec_prenom]
              + [fiche(t, "nom_seul") for t in sur_le_nom if id(t) not in ids])
    return {
        "total": len(sur_le_nom),
        "nom_et_prenom": len(avec_prenom),
        "nom_seul": len(sur_le_nom) - len(avec_prenom),
        "correspondances": fiches[:limit],
    }


def verifier_fraicheur(publie_le: datetime, verifie_le: datetime, maintenant: datetime) -> None:
    """Lève `RegistreInvalide` si une copie gardée ne permet plus de conclure.

    `verifie_le` = la dernière lecture réussie de la source (qui a confirmé que cette
    publication est la dernière). Tant qu'elle date de moins de `2 × GARDE`, la copie
    EST le registre en vigueur, quelle que soit sa date de publication. Au-delà, la
    source ne répond plus : la copie ne sert que si sa publication a moins de
    `PUBLICATION_MAX`.
    """
    if maintenant - verifie_le <= 2 * GARDE:
        return
    if maintenant - publie_le < PUBLICATION_MAX:
        return
    raise RegistreInvalide(
        f"registre des gels d'avoirs trop ancien : publié le {publie_le.date().isoformat()}, "
        f"source non relue depuis le {verifie_le.date().isoformat()}")
