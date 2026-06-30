"""Egapro — Index égalité professionnelle femmes-hommes (open data, sans clé).

Toute entreprise d'au moins 50 salariés doit déclarer chaque année son index
d'égalité F-H (note /100) sur Egapro (DGT, ministère du Travail). L'API publique
sert une déclaration par `(siren, année)` : identité, **effectif total exact**
(là où SIRENE ne donne qu'une tranche), code NAF, et les notes par indicateur.

Source : https://egapro.travail.gouv.fr/api/public — lookup `/declaration/{siren}/{year}`.
Pas de recherche en masse ici (le sourcing reste l'API recherche-entreprises) :
c'est un outil de **qualification** d'un SIREN déjà identifié.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import requests

BASE = "https://egapro.travail.gouv.fr/api/public"


class EgaproClient:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()

    def declaration(self, siren: str, year: int) -> Optional[dict[str, Any]]:
        """Déclaration Egapro d'un SIREN pour une année (None si aucune).

        Renvoie le payload brut `{entreprise, indicateurs, déclaration}` enrichi
        de `annee`. `entreprise.effectif.total` = effectif exact déclaré.
        """
        r = self.session.get(f"{BASE}/declaration/{siren}/{year}", timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        out = r.json()
        out["annee"] = year
        return out

    def latest_declaration(self, siren: str, *, max_back: int = 5) -> Optional[dict[str, Any]]:
        """Déclaration la plus récente d'un SIREN (balaye les `max_back` dernières années).

        L'API ne liste pas les années d'un SIREN : on essaie de l'année courante
        vers le passé et on renvoie la première trouvée (None si aucune).
        """
        current = datetime.now().year
        for year in range(current, current - max_back, -1):
            decl = self.declaration(siren, year)
            if decl is not None:
                return decl
        return None
