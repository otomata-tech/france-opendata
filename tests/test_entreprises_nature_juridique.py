"""Recherche Entreprises — filtre `nature_juridique` (forme juridique), offline.

Motivation : « SCI ASC » rend 38 sociétés littéralement NOMMÉES "SCI ASC" et
jamais la SCI immatriculée "ASC" (SIREN 921960159, forme 6540) — or nommer la
forme devant le nom est la façon naturelle de désigner une SCI à l'oral. La
forme appartient à un filtre, pas au texte cherché. Mesuré en live le 30/07/2026 :
q=ASC → 1580 résultats, q=ASC + nature_juridique=6540 → 98, cible en page 1.
"""
import pytest

from france_opendata import entreprises as E
from france_opendata.entreprises import EntreprisesClient


class _Resp:
    ok = True
    status_code = 200

    def json(self):
        return {"results": [], "total_results": 0}


@pytest.fixture()
def sent(monkeypatch):
    """Capture les params de la requête sortante au lieu de l'émettre."""
    box = {}

    def _get(url, params=None, timeout=None, **kw):
        box.update(params or {})
        return _Resp()

    monkeypatch.setattr(E.requests, "get", _get)
    return box


def test_nature_juridique_is_sent_as_a_filter(sent):
    EntreprisesClient().search(query="ASC", nature_juridique=["6540"])
    assert sent["nature_juridique"] == "6540"
    assert sent["q"] == "ASC"


def test_several_forms_are_comma_joined(sent):
    EntreprisesClient().search(query="ASC", nature_juridique=["6540", "6599"])
    assert sent["nature_juridique"] == "6540,6599"


def test_nature_juridique_alone_is_a_valid_search(sent):
    """Il compte comme critère : « toutes les SCI du 13 » ne doit pas être rejeté
    faute d'un autre paramètre."""
    EntreprisesClient().search(nature_juridique=["6540"])
    assert sent["nature_juridique"] == "6540"


def test_search_without_any_criterion_still_raises():
    with pytest.raises(ValueError):
        EntreprisesClient().search()
