"""ODRÉ — l'étage transport, et ce que sa maille permet de dire.

Offline : `_signal` est une fonction pure.
"""
from france_opendata.odre import _signal


def _iris(code="330320104", mwh=49970.0, pdl=1, annee="2023-01-01T00:00:00+00:00"):
    return {
        "code_iris": code, "consommation_electricite_rte": mwh, "pdl_electricite_rte": pdl,
        "annee": annee, "code_insee_commune": "33032", "commune": "Bassens",
        "code_insee_departement": "33", "departement": "Gironde", "code_insee_region": "75",
    }


def test_un_seul_point_de_livraison_vaut_pour_un_site():
    s = _signal(_iris(pdl=1))
    assert s["maille"] == "site" and s["site_unique"] is True
    assert s["reseau"] == "transport"
    assert s["annee"] == "2023"          # la date ISO est ramenée à l'année
    assert s["ref_key"] == "330320104|2023"


def test_plusieurs_points_de_livraison_restent_une_somme():
    """Saint-Jean-de-Maurienne : 3 pdl. La valeur est vraie, ce n'est pas un site."""
    s = _signal(_iris(code="732480101", mwh=1702616.0, pdl=3))
    assert s["maille"] == "iris_agrege" and s["site_unique"] is False
    assert s["mwh"] == 1702616.0        # on ne cache pas la valeur, on qualifie sa maille


def test_une_ligne_purement_gaziere_est_ecartee():
    """Pas d'électricité sur la ligne : ce n'est pas un zéro, c'est une absence."""
    assert _signal(_iris(mwh=None)) is None


def test_sans_iris_pas_de_signal():
    assert _signal(_iris(code=None)) is None
