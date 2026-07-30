"""Sit@del — filtre par DEMANDEUR (SIREN/SIRET), offline.

`search()` n'exposait que la géo et l'année : « les permis déposés par cette
société » imposait de paginer une commune entière puis de filtrer côté client
(295 permis logements + 260 locaux pour Amiens, réponse rendue incomplète).
`SIREN_DEM` étant une colonne DiDo filtrable côté serveur, le scope géographique
n'est même plus requis — vérifié en live : SIREN_DEM=eq:585980022 → 142 permis
France entière, sans COMM ni DEP_CODE.
"""
from france_opendata.sitadel import RID_BY_KIND, SitadelClient


class _Resp:
    status_code = 200

    def raise_for_status(self):
        ...

    def json(self):
        return {"total": 0, "page": 1, "data": []}


class _Session:
    """Capture les params de la requête DiDo au lieu de l'émettre."""

    def __init__(self):
        self.params = None
        self.url = None

    def get(self, url, params=None, timeout=None):
        self.url, self.params = url, params
        return _Resp()


def _client():
    c = SitadelClient()
    c.session = _Session()
    return c


def test_siren_becomes_a_server_side_eq_filter():
    c = _client()
    c.search("logements", siren="585980022")
    assert c.session.params["SIREN_DEM"] == "eq:585980022"
    assert RID_BY_KIND["logements"] in c.session.url


def test_siren_alone_needs_no_geographic_scope():
    """Le point du correctif : une recherche par société est NATIONALE — aucun
    COMM/DEP_CODE n'est ajouté d'office, sinon on retombe sur la pagination
    commune par commune qui a motivé le signal."""
    c = _client()
    c.search("locaux", siren="585980022")
    assert "COMM" not in c.session.params and "DEP_CODE" not in c.session.params


def test_siret_and_geography_combine():
    c = _client()
    c.search("logements", siret="58598002200012", communes="80021", an_min=2024)
    p = c.session.params
    assert p["SIRET_DEM"] == "eq:58598002200012"
    assert p["COMM"] == "eq:80021"
    assert p["AN_DEPOT"] == "gte:2024"


def test_no_demandeur_filter_leaves_params_untouched():
    c = _client()
    c.search("logements", communes="80021")
    assert "SIREN_DEM" not in c.session.params and "SIRET_DEM" not in c.session.params
