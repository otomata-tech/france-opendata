"""Téléchargement du registre national des gels d'avoirs (DG Trésor) → publication + lignes.

Le registre entier se publie en un fichier JSON (~13 Mo), public, sans clé. Ce module ne
fait que le lire et le valider (`gels_avoirs.valider`) ; le stockage est au consommateur.
Le service FOD le relit toutes les six heures (`gels_avoirs.GARDE`) et le garde en
PostgreSQL, remplacé d'un bloc : un contrôle ne dépend pas du débit de la source.

Gotcha : l'hôte coupe parfois la connexion (`Connection reset by peer`) sur des
téléchargements rapprochés — constaté le 16/10/2026 depuis un poste, trois refus de
suite après un succès. Un échec n'est pas une absence : on lève, et la copie déjà
gardée continue de servir tant qu'elle est fraîche (`gels_avoirs.verifier_fraicheur`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import requests

from . import gels_avoirs


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "france-opendata/gels-avoirs (+https://github.com/otomata-tech/france-opendata)"
    s.headers["Accept"] = "application/json"
    return s


def telecharger(sess: Optional[requests.Session] = None, timeout: int = 120) -> tuple[datetime, list[dict]]:
    """La dernière publication du registre : `(date de publication, lignes)`.

    Lève `requests.RequestException` si la source ne répond pas, et
    `gels_avoirs.RegistreInvalide` si ce qu'elle rend ne permet pas de conclure.
    """
    sess = sess or _session()
    resp = sess.get(gels_avoirs.URL, timeout=timeout)
    resp.raise_for_status()
    registre = gels_avoirs.valider(resp.json())
    return gels_avoirs.publication(registre), gels_avoirs.inscrits(registre)
