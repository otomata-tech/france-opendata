"""Refus d'entrée : `ParametreInvalide` nomme le paramètre, avant tout appel amont.

Et la fiche d'un SIREN ne rend jamais une autre entreprise. Offline.
"""
import pytest

from france_opendata import entreprises as E
from france_opendata.entreprises import EntreprisesClient
from france_opendata.erreurs import ParametreInvalide
from france_opendata.georisques import GeorisquesClient
from france_opendata.lannuaire import LannuaireClient
from france_opendata.sitadel import SitadelClient


class _Resp:
    ok = True
    status_code = 200

    def __init__(self, results):
        self._results = results

    def json(self):
        return {"results": self._results, "total_results": len(self._results)}


def _api(monkeypatch, results):
    envoye = {}

    def _get(url, params=None, timeout=None, **kw):
        envoye.update(params or {})
        return _Resp(results)

    monkeypatch.setattr(E.requests, "get", _get)
    return envoye


# --- fiche par SIREN ----------------------------------------------------------

def test_un_siren_inconnu_ne_rend_pas_le_premier_resultat_approchant(monkeypatch):
    """La recherche plein texte rend des sociétés voisines : aucune n'est la bonne."""
    _api(monkeypatch, [{"siren": "552100554", "nom_complet": "AUTRE SOCIETE"}])
    assert EntreprisesClient().get_by_siren("552100555") is None


def test_un_siren_connu_rend_sa_fiche_meme_hors_premiere_place(monkeypatch):
    _api(monkeypatch, [{"siren": "111111111"}, {"siren": "552100554", "nom_complet": "LA BONNE"}])
    fiche = EntreprisesClient().get_by_siren("552100554")
    assert fiche["siren"] == "552100554" and fiche["nom_complet"] == "LA BONNE"


def test_dirigeants_d_un_siren_inconnu_vide(monkeypatch):
    _api(monkeypatch, [{"siren": "552100554", "dirigeants": [{"nom": "X"}]}])
    assert EntreprisesClient().get_directors("999999999") == []


# --- recherche d'entreprises --------------------------------------------------

def _refus(fonction, parametre):
    with pytest.raises(ParametreInvalide) as e:
        fonction()
    assert e.value.parametre == parametre and str(e.value).startswith(parametre)
    return e.value


def test_recherche_sans_critere_nomme_les_criteres():
    erreur = _refus(lambda: EntreprisesClient().search(),
                    "query, naf, departement, code_postal, commune, employees, "
                    "categorie_entreprise, ca_min, ca_max, idcc, nature_juridique")
    assert isinstance(erreur, ValueError)  # les appelants qui attrapaient ValueError


def test_plus_d_un_idcc_est_refuse():
    _refus(lambda: EntreprisesClient().search(query="x", idcc=["1285", "3090"]), "idcc")


@pytest.mark.parametrize("seuil", ["ca_min", "ca_max"])
@pytest.mark.parametrize("valeur", [0, -5])
def test_un_seuil_de_ca_nul_ou_negatif_est_refuse(seuil, valeur):
    """Avant : 0 tombait en silence (`if ca_min:`), et seul critère, en 500."""
    _refus(lambda: EntreprisesClient().search(**{seuil: valeur}), seuil)


def test_un_seuil_de_ca_positif_est_transmis(monkeypatch):
    envoye = _api(monkeypatch, [])
    EntreprisesClient().search(ca_min=1_000_000)
    assert envoye["ca_min"] == 1_000_000


# --- autres clients -----------------------------------------------------------

def test_icpe_sans_siret_ni_commune():
    _refus(lambda: GeorisquesClient().installations_classees(), "siret, code_insee")


def test_annuaire_sans_siren_ni_commune():
    _refus(lambda: LannuaireClient().services(), "siren, code_commune")


@pytest.mark.parametrize("page_size", [0, 30, 1000])
def test_sitadel_page_size_hors_des_valeurs_dido(page_size):
    _refus(lambda: SitadelClient().search("logements", dept="59", page_size=page_size), "page_size")
