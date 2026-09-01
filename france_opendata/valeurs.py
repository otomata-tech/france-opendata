"""Une absence ne se déguise pas en valeur.

Les sources amont écrivent parfois l'absence sous forme de **texte** : `undefined`,
`null`, `none`, `NaN`, `N/A`. Le réflexe habituel — `donnee.get("champ") or None` —
ne les attrape pas : une chaîne non vide est vraie en Python, donc la sentinelle
traverse et atterrit en base comme si c'était une donnée.

Coût mesuré du défaut (2026-09-01) : **251 942 décisions de jurisprudence** portaient
la chaîne `undefined` en identifiant européen — 15 % du corpus. Conséquence concrète :
un regroupement par cet identifiant les rassemble toutes sous une même clé et fait
conclure à un gigaoctet de doublons inexistants. Un audit d'occupation disque a failli
partir sur cette fausse piste.

C'est la même faute que celles corrigées ailleurs dans cette lib — un `0` qui code un
chiffre d'affaires non déclaré (`finances`), une sentinelle qui code un montant
illisible (`liasse`). À la différence près que celle-ci, nous la produisions
nous-mêmes en recopiant l'amont sans le regarder.
"""
from __future__ import annotations

from typing import Any, Optional

#: Formes textuelles d'une absence, rencontrées chez les producteurs. Comparées en
#: minuscules et sans espaces.
#:
#: ⚠️ N'y mettre QUE des formes qui ne peuvent pas être une valeur légitime. `-1`,
#: `0` et `-` en ont été écartés : ce sont des valeurs réelles dans d'autres champs
#: (un solde, un identifiant, un tiret de titre). Élargir cette liste pour attraper
#: un cas de plus, c'est risquer d'effacer une donnée vraie — soit exactement le
#: défaut qu'on corrige, retourné.
SENTINELLES = frozenset({
    "undefined", "null", "none", "nan", "n/a", "nil", "(null)",
})


def texte(valeur: Any) -> Optional[str]:
    """Rend une chaîne utile, ou `None` — jamais une absence déguisée.

    À utiliser partout où l'on recopie un champ optionnel d'une source amont, à la
    place de `valeur or None` qui laisse passer les sentinelles textuelles.

    >>> texte("ECLI:FR:CCASS:2026:CR00922")
    'ECLI:FR:CCASS:2026:CR00922'
    >>> texte("undefined") is None
    True
    >>> texte("  ") is None
    True
    """
    if valeur is None:
        return None
    s = str(valeur).strip()
    if not s or s.lower() in SENTINELLES:
        return None
    return s
