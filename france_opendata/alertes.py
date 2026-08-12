"""Vocabulaire fermé des alertes de donnée — le contrat, pas de la prose.

Une alerte marque un montant qu'on ne peut pas servir tel quel. Elle est destinée à
être LUE PAR DU CODE : un consommateur écrit `if "non_declare" in entry["alerte"]`,
pas une expression régulière sur une phrase. D'où l'énumération fermée ci-dessous,
publiée dans le schéma OpenAPI du service (§`ALERTES`) — un client peut en générer
un type, et un code retiré est un **breaking change de contrat** (en ajouter un est
additif).

**Chaque code porte sa propre certitude.** C'est la règle qui fonde ce module : une
annotation qui surestime sa confiance est exactement le mensonge qu'elle prétend
corriger, un cran plus haut. Deux niveaux, et le NOM les distingue :

- `PROUVE` — établi par la donnée elle-même (une sentinelle présente, un 0 servi).
  Aucun jugement : on rapporte ce qu'on lit.
- `INFERE` — l'explication la plus probable, mesurée mais pas certaine. Un faux
  positif reste possible ; le nom du code doit donc porter le doute (`_probable`),
  jamais l'affirmer.

Corollaire, aussi important que le premier : **on ne sous-estime pas non plus sa
certitude**. Taire un fait prouvé sous prétexte que sa cause est incertaine ferait
disparaître l'information qu'on possède — c'est le défaut d'origine, déplacé.
"""
from __future__ import annotations

PROUVE = "prouve"
INFERE = "infere"

#: code → (certitude, description). Source unique du vocabulaire : le service en
#: dérive son OpenAPI, les annotateurs n'émettent que ces codes.
ALERTES: dict[str, tuple[str, str]] = {
    "non_declare": (
        PROUVE,
        "L'amont code l'absence de donnée par un 0. Ce n'est pas un chiffre "
        "d'affaires nul : l'entreprise peut en avoir un, et considérable.",
    ),
    "valeur_aberrante": (
        PROUVE,
        "Montant négatif, illisible comme produit d'exploitation. La valeur reçue "
        "est conservée sous `ca_valeur_amont` pour inspection.",
    ),
    "invraisemblable": (
        INFERE,
        "Le montant ne tient pas face à l'effectif (`ca_par_salarie` donne le "
        "ratio). Causes possibles mais NON établies : dépôt libellé en milliers, "
        "association vivant de subventions, holding portant les salariés d'un "
        "groupe. Le constat est nommé, jamais sa cause.",
    ),
    "valeur_indisponible": (
        PROUVE,
        "Le poste porte la sentinelle du parquet (INT32_MAX) : sa valeur n'est pas "
        "lisible. Le poste EXISTE dans le dépôt — ne pas confondre avec un poste "
        "absent. Les codes concernés sont listés dans `postes_indisponibles`.",
    ),
    "saturation_probable": (
        INFERE,
        "Le poste indisponible s'explique très probablement par un débordement "
        "INT32 (montant > 2 147 483 647), et non par une absence : la ligne porte "
        "d'autres montants très élevés, ou sature massivement. La valeur exacte est "
        "détruite en amont et ne peut pas être reconstituée.",
    ),
}

#: Les valeurs admissibles, pour l'`enum` du schéma publié.
CODES: tuple[str, ...] = tuple(ALERTES)


def certitude(code: str) -> str:
    """`PROUVE` ou `INFERE` — ce que le code engage."""
    return ALERTES[code][0]
