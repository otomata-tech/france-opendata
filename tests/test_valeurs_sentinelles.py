"""Une absence ne doit jamais traverser sous forme de texte.

Défaut mesuré le 2026-09-01 : 251 942 décisions de jurisprudence (15 % du corpus)
portaient la chaîne `undefined` en identifiant européen. Vecteur : le motif
`donnee.get("champ") or None`, qui protège de la chaîne vide et de None mais **pas**
d'une sentinelle textuelle — une chaîne non vide est vraie.

Conséquence concrète : un regroupement par cet identifiant rassemble les 251 942
lignes sous une seule clé et fait conclure à ~1 Go de doublons inexistants. Un audit
d'occupation disque a failli partir sur cette fausse piste.
"""
from __future__ import annotations

import pytest

from france_opendata import valeurs


@pytest.mark.parametrize("sentinelle", [
    "undefined", "UNDEFINED", " undefined ", "null", "NULL", "None", "none",
    "nan", "NaN", "n/a", "N/A", "nil", "(null)", "", "   ",
])
def test_a_textual_absence_becomes_a_real_absence(sentinelle):
    assert valeurs.texte(sentinelle) is None


@pytest.mark.parametrize("vraie", [
    "ECLI:FR:CCASS:2026:CR00922",
    "Cass. civ. 1re",
    # ⚠️ Ces trois-là sont des VALEURS, pas des absences. Les ajouter aux
    # sentinelles pour attraper un cas de plus effacerait de la donnée vraie —
    # le défaut qu'on corrige, retourné.
    "0", "-1", "-",
])
def test_a_real_value_is_never_mistaken_for_an_absence(vraie):
    assert valeurs.texte(vraie) == vraie


def test_none_stays_none_and_types_are_normalised():
    assert valeurs.texte(None) is None
    assert valeurs.texte(42) == "42"          # un entier reste une valeur
    assert valeurs.texte("  espacé  ") == "espacé"


def test_the_vulnerable_pattern_is_gone_from_the_jurisprudence_sources():
    """Tripwire : `X.get(…) or None` laisse passer les sentinelles. Aucune source de
    jurisprudence ne doit le réintroduire — c'est par là que les 251 942 sont
    entrées."""
    import pathlib
    import re
    racine = pathlib.Path(__file__).resolve().parent.parent / "france_opendata"
    motif = re.compile(r'\w+\.get\([^)]*\)\s+or\s+None')
    coupables = {
        f.name: motif.findall(f.read_text(encoding="utf-8"))
        for f in (racine / n for n in ("judilibre.py", "jade_live.py", "cedh.py"))
    }
    assert not any(coupables.values()), (
        f"motif vulnérable réintroduit : {coupables} — utiliser valeurs.texte()")
