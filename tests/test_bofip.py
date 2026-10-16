"""BOFiP : ce que la recherche et la lecture demandent au portail, et ce qu'elles rendent.

Offline : `OpendatasoftClient.records` est remplacé par un enregistreur.
"""
import pytest

from france_opendata import bofip
from france_opendata.bofip import BofipClient


class _Ods:
    def __init__(self, results, total=None):
        self.results, self.total, self.appels = results, total, []

    def records(self, dataset, **kw):
        self.appels.append((dataset, kw))
        return {"total_count": self.total if self.total is not None else len(self.results),
                "results": [dict(r) for r in self.results]}


def _client(results, total=None):
    c = BofipClient()
    c.ods = _Ods(results, total)
    return c


PUB = {"identifiant_juridique": "BOI-ENR-DMTG-20-30-20-20", "titre": "ENR - Abattements",
       "serie": "ENR", "type": "Contenu", "debut_de_validite": "2024-03-19",
       "permalien": "https://bofip.impots.gouv.fr/bofip/3351-PGP.html/identifiant=BOI-ENR-DMTG-20-30-20-20-20240319"}


def test_litteral_ne_se_ferme_pas():
    assert bofip.litteral(' pacte "Dutreil\\ ') == '"pacte  Dutreil"'


def test_extrait_autour_du_premier_mot_trouve():
    texte = "x " * 300 + "le pacte Dutreil " + "y " * 300
    ex = bofip.extrait_autour(texte, ["dutreil", "pacte"])
    assert ex.startswith("…") and ex.endswith("…") and "pacte Dutreil" in ex
    assert len(ex) == bofip.EXTRAIT + 2


def test_extrait_sans_mot_trouve_prend_le_debut():
    assert bofip.extrait_autour("Début   du\ntexte", ["absent"]) == "Début du texte"


def test_recherche_demande_tous_les_mots_parmi_les_commentaires():
    c = _client([{**PUB, "contenu": "… l'abattement prévu à l'article 790 G du CGI …"}], total=12)
    r = c.rechercher("abattement donation")
    dataset, kw = c.ods.appels[0]
    assert dataset == "bofip-vigueur"
    assert kw["where"] == '"abattement donation" and type="Contenu"'
    assert kw["limit"] == 6 and "contenu" in kw["select"] and "contenu_html" not in kw["select"]
    assert r["total_count"] == 12
    p = r["publications"][0]
    assert "contenu" not in p and "abattement" in p["extrait"]
    assert p["permalien"] == PUB["permalien"]


def test_recherche_vide_n_est_pas_une_erreur():
    assert _client([]).rechercher("aucun mot connu") == {"total_count": 0, "publications": []}


def test_recherche_trop_courte_refusee():
    with pytest.raises(ValueError):
        _client([]).rechercher(" a ")


def test_lire_retire_la_date_de_version():
    c = _client([{**PUB, "contenu": "ligne 1\r\nligne 2  "}])
    r = c.lire("BOI-ENR-DMTG-20-30-20-20-20240319")
    assert c.ods.appels[0][1]["where"] == 'identifiant_juridique="BOI-ENR-DMTG-20-30-20-20"'
    assert r["contenu"] == "ligne 1\nligne 2"


def test_lire_une_reference_inconnue_rend_none():
    assert _client([]).lire("BOI-ENR-XXX-10") is None


@pytest.mark.parametrize("ref", ["boi-enr-dmtg", "ENR-DMTG-10", 'BOI-ENR" or 1=1'])
def test_lire_une_reference_mal_formee_leve_avant_tout_appel(ref):
    c = _client([])
    with pytest.raises(ValueError):
        c.lire(ref)
    assert c.ods.appels == []
