"""Les erreurs d'ENTRÉE d'un client : un paramètre absent, mal formé ou hors bornes.

`ParametreInvalide` hérite de `ValueError` (les appelants qui l'attrapaient déjà ne
changent pas) et nomme le paramètre fautif. Elle distingue un refus d'entrée — que le
consommateur rend à son appelant (le service FOD : 400 qui nomme le paramètre) — d'une
`ValueError` quelconque, qui reste un défaut de code et doit continuer d'échouer
bruyamment. Le message ne recopie pas la valeur reçue quand elle peut être une donnée
personnelle (un nom, un SIREN d'entrepreneur individuel).
"""
from __future__ import annotations


class ParametreInvalide(ValueError):
    """Un paramètre d'entrée refusé avant tout appel amont."""

    def __init__(self, parametre: str, message: str):
        self.parametre = parametre
        super().__init__(f"{parametre} : {message}")
