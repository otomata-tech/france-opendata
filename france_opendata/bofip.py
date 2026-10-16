"""BOFiP-Impôts — la doctrine fiscale en vigueur (DGFiP), en open data.

Source : jeu Opendatasoft `bofip-vigueur` sur data.economie.gouv.fr, sans clé.
  https://data.economie.gouv.fr/explore/dataset/bofip-vigueur/
Licence : Licence Ouverte v2.0 (Etalab) — métadonnées du jeu sur le portail même
(`metas.default.license`, éditeur DGFIP), lues le 16/10/2026.

Ce que dit l'administration fiscale des droits de mutation, des plus-values, de l'IFI,
du pacte Dutreil… à côté de la loi et de la jurisprudence. **La doctrine n'est pas la
loi** : qui la cite doit le dire (`MENTION`).

Deux lectures :
- `rechercher(query)` : les commentaires (`type="Contenu"`) qui portent TOUS les mots,
  avec un extrait autour du premier mot significatif trouvé ;
- `lire(identifiant)` : un commentaire ou une actualité par sa référence BOI
  (`BOI-ENR-DMTG-20-30-20-20`), texte intégral compris.

Gotchas :
- `identifiant_juridique` ne porte PAS la date de version ; le permalien, si
  (`…identifiant=BOI-ENR-DMTG-20-30-20-20-20240319`). Une référence recopiée d'un
  permalien ou d'un texte daté finit donc par `-AAAAMMJJ` : `lire` la retire avant de
  chercher, sinon elle ne trouverait rien.
- Le portail est opéré par Opendatasoft : la recherche y part en clair (mots, référence).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .opendatasoft import OpendatasoftClient

PORTAL = "https://data.economie.gouv.fr"
DATASET = "bofip-vigueur"
PAGE = f"{PORTAL}/explore/dataset/{DATASET}/"
LICENCE = "Licence Ouverte v2.0 (Etalab)"
PRODUCTEUR = "Direction générale des Finances publiques (DGFiP)"
MENTION = ("Source : BOFiP-Impôts, publications en vigueur (DGFiP, open data "
           "data.economie.gouv.fr, Licence Ouverte v2.0). Doctrine administrative, pas la loi.")

#: Les champs rendus — `contenu_html` n'est jamais demandé (doublon du texte, en HTML).
CHAMPS = "identifiant_juridique,titre,serie,type,debut_de_validite,permalien"
#: Longueur d'un extrait de recherche, en caractères.
EXTRAIT = 400
#: Une référence BOI (commentaire) ou ACTU (actualité).
IDENTIFIANT_RE = re.compile(r"^(BOI|ACTU)-[A-Z0-9-]+$")
_DATE_FINALE = re.compile(r"-\d{8}$")


def litteral(texte: str) -> str:
    """Une valeur dans une requête ODSQL, entre guillemets, sans guillemet ni barre
    oblique inverse qui la fermerait."""
    return '"' + re.sub(r'["\\]', " ", texte).strip() + '"'


def extrait_autour(contenu: str, mots: list[str]) -> str:
    """Un extrait de `EXTRAIT` caractères autour du premier mot trouvé dans le texte,
    sinon son début. Les coupes sont marquées par « … »."""
    texte = re.sub(r"\s+", " ", contenu).strip()
    minuscule = texte.lower()
    positions = sorted(p for p in (minuscule.find(m.lower()) for m in mots) if p >= 0)
    position = positions[0] if positions else 0
    debut = max(0, position - EXTRAIT // 4)
    return (("…" if debut > 0 else "") + texte[debut:debut + EXTRAIT]
            + ("…" if debut + EXTRAIT < len(texte) else ""))


def identifiant_sans_date(identifiant: str) -> str:
    """La référence BOI telle que le jeu l'indexe : sans le suffixe de version `-AAAAMMJJ`."""
    return _DATE_FINALE.sub("", identifiant.strip())


class BofipClient:
    """Doctrine fiscale en vigueur (BOFiP-Impôts). Sans clé."""

    def __init__(self, timeout: int = 30):
        self.ods = OpendatasoftClient(PORTAL, timeout=timeout)

    def rechercher(self, query: str, limit: int = 6) -> dict[str, Any]:
        """Les commentaires en vigueur qui portent tous les mots de `query`.

        Renvoie `{"total_count", "publications": [{identifiant_juridique, titre, serie,
        type, debut_de_validite, permalien, extrait}]}`. Zéro résultat n'est pas une
        erreur : moins de mots, ou d'autres, en trouveront.
        """
        query = query.strip()
        if len(query) < 3:
            raise ValueError("query : au moins 3 caractères (2 à 5 mots-clés)")
        page = self.ods.records(
            DATASET, where=f'{litteral(query)} and type="Contenu"',
            select=CHAMPS + ",contenu", limit=limit)
        mots = [m for m in query.split() if len(m) > 3]
        publications = []
        for r in page.get("results", []):
            contenu = r.pop("contenu", None)
            publications.append({**r, "extrait": extrait_autour(contenu, mots) if contenu else None})
        return {"total_count": page.get("total_count", 0), "publications": publications}

    def lire(self, identifiant: str) -> Optional[dict[str, Any]]:
        """Un commentaire ou une actualité en vigueur, texte intégral (`contenu`) compris.

        `None` si aucune publication en vigueur ne porte cette référence. Une référence
        mal formée lève `ValueError` avant tout appel.
        """
        ref = identifiant_sans_date(identifiant)
        if not IDENTIFIANT_RE.match(ref):
            raise ValueError(f"référence BOI attendue (BOI-… ou ACTU-…), reçu {identifiant!r}")
        page = self.ods.records(
            DATASET, where=f"identifiant_juridique={litteral(ref)}",
            select=CHAMPS + ",contenu", limit=1)
        results = page.get("results", [])
        if not results:
            return None
        r = results[0]
        r["contenu"] = (r.get("contenu") or "").replace("\r\n", "\n").strip()
        return r
