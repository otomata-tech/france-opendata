"""Honnêteté de la liasse INPI : la sentinelle du parquet dit deux choses (#10).

Le parquet « Données financières détaillées » stocke la liasse en `MAP(VARCHAR,
INTEGER)`, donc en **INT32** — plafond 2 147 483 647. Cette même valeur y sert de
sentinelle « poste absent », et encaisse aussi tout montant qui déborde. Les deux
cas étaient confondus puis **retirés**, si bien qu'un poste débordé devenait
indistinguable d'un poste jamais déposé : Michelin sort sans chiffre d'affaires sur
4 exercices alors que l'API Recherche Entreprises en donne 5 570 764 860 € pour 2024.

Ce module ne restitue AUCUN montant — la valeur exacte est détruite au build du
parquet, en amont de nous (le producteur devrait publier en INT64). Il sépare ce
qu'on sait de ce qu'on infère :

- la **présence** de la sentinelle est un fait : le poste existe, sa valeur n'est
  pas lisible → `valeur_indisponible` ;
- le **débordement** comme cause est une inférence mesurée → `saturation_probable`.

⚠️ **La forme est ADDITIVE.** `liasse` continue de ne porter que les postes lisibles
— y réinjecter la sentinelle ferait sortir un chiffre d'affaires de 2 147 483 647,
franc et entièrement inventé, soit un défaut pire que celui qu'on corrige. Les codes
concernés sortent à côté, dans `postes_indisponibles`.
"""
from __future__ import annotations

from typing import Any

from .alertes import ALERTES  # noqa: F401  (le vocabulaire fermé fait foi)

#: Valeur sentinelle du parquet (INT32_MAX) : « absent » OU « a débordé ».
MISSING = 2147483647

# Seuils de la règle discriminante. Mesurés sur les 2 688 lignes à `FL` saturé,
# comparées aux lignes normales : 24,4 postes > 100 M€ par ligne saturée contre
# 0,37 — facteur 66. Et les plus grandes valeurs non saturées de ces lignes frôlent
# le plafond sans le franchir (1 999 700 000 · 1 976 700 000 · 1 836 600 000).
_GROS_POSTE = 100_000_000

# Seconde branche, pour les lignes qui ne portent AUCUN gros poste non saturé.
# Au grain ligne : 84,5 % des lignes saturées portent 10 gros postes ou plus,
# 13,7 % en portent 3 à 9, 1,5 % en portent 1 à 2 — et les 0,3 % restantes (9
# lignes) n'en portent aucun, parce qu'elles saturent presque partout : 29 postes
# à la sentinelle en moyenne, contre 12,9 sur une ligne saturée ordinaire. Sans
# cette branche, ce sont précisément les plus grandes entreprises du lot qu'on
# raterait. Le seuil est bas au regard de cette moyenne : plusieurs postes portant
# tous EXACTEMENT le plafond ne se lit pas comme une donnée réelle.
_SATURATION_MASSIVE = 10


def split(liasse: Any) -> tuple[dict[str, int], list[str]]:
    """MAP brute → `(postes lisibles, codes dont la valeur est indisponible)`.

    Les `None` sont écartés sans trace : le poste n'est pas au dépôt. La sentinelle,
    elle, est un poste PRÉSENT dont la valeur ne peut pas être lue — c'est ce que le
    second membre conserve, et que l'ancien filtre jetait.
    """
    postes: dict[str, int] = {}
    indisponibles: list[str] = []
    for code, valeur in (liasse or {}).items():
        if valeur is None:
            continue
        if valeur == MISSING:
            indisponibles.append(code)
        else:
            postes[code] = valeur
    return postes, sorted(indisponibles)


def alertes(postes: dict[str, int], indisponibles: list[str]) -> list[str]:
    """Les alertes que porte cet exercice. Vide si tout est lisible.

    `valeur_indisponible` dès qu'un poste est indisponible — c'est le fait, il ne
    dépend d'aucune heuristique. `saturation_probable` ne s'y ajoute que si la ligne
    corrobore le débordement ; à défaut on se tait sur la CAUSE, jamais sur le fait.
    """
    if not indisponibles:
        return []
    out = ["valeur_indisponible"]
    gros = any(v > _GROS_POSTE for v in postes.values())
    if gros or len(indisponibles) >= _SATURATION_MASSIVE:
        out.append("saturation_probable")
    return out


def annotate(record: dict[str, Any], postes: dict[str, int],
             indisponibles: list[str]) -> dict[str, Any]:
    """Pose `postes_indisponibles` + `alerte` sur un exercice, si besoin.

    Un exercice entièrement lisible n'est pas alourdi de clés vides : leur PRÉSENCE
    est le signal.
    """
    codes = alertes(postes, indisponibles)
    if codes:
        record["postes_indisponibles"] = indisponibles
        record["alerte"] = codes
    return record
