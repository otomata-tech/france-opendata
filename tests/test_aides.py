"""AidesClient — filtre déterministe sur fixtures synthétiques (pas de réseau).

La hiérarchie géo réelle (Toulouse ,56,18,50007,1,0,) et l'entonnoir mesuré
2431→441→138 ont été validés contre les vrais dumps le 2026-07-17 (signal oto #232) ;
ici on fige la LOGIQUE : matching par ancêtres, tranches d'effectif, natures,
échéances, filtre lexical, erreurs actionnables.
"""
from __future__ import annotations

import pytest

from france_opendata.aides import AidesClient, _codes_for_effectif, _date_fin, _text

TERRITOIRES = [
    {"id_ter": "0", "insee": None, "ter_code": "", "parents": "", "DEL": "0", "status": "1"},
    {"id_ter": "1", "insee": "", "ter_code": "FR", "parents": ",0,", "DEL": "0", "status": "1"},
    # région → département → commune
    {"id_ter": "50007", "insee": "", "ter_code": "OCC", "parents": ",1,0,", "DEL": "0", "status": "1"},
    {"id_ter": "56", "insee": "", "ter_code": "31", "parents": ",50007,1,0,", "DEL": "0", "status": "1"},
    {"id_ter": "12550", "insee": "31555", "ter_code": "31000",
     "parents": ",56,50007,1,0,", "DEL": "0", "status": "1"},
    {"id_ter": "999", "insee": "75103", "ter_code": "75003",
     "parents": ",1,0,", "DEL": "0", "status": "1"},
    # Marseille : le référentiel n'a QUE les arrondissements (pas de 13055)
    {"id_ter": "55118", "insee": "13201", "ter_code": "13001",
     "parents": ",37,1,0,", "DEL": "0", "status": "1"},
    {"id_ter": "55119", "insee": "13202", "ter_code": "13002",
     "parents": ",37,1,0,", "DEL": "0", "status": "1"},
]


def _aide(id_aid, ter_ids, *, nom="Aide", objet="", effectif="", date_fin="0000-00-00 00:00:00",
          natures=(), status="1", couverture_geo="1"):
    return {
        "id_aid": id_aid, "aid_nom": nom, "aid_objet": objet, "aid_conditions": "",
        "aid_benef": "", "aid_montant": "", "effectif": effectif, "date_fin": date_fin,
        "status": status, "couverture_geo": couverture_geo, "complements": None,
        "cache_indexation": {
            "territoires": [{"id_ter": t} for t in ter_ids],
            "natures": [{"typ_libelle": n} for n in natures],
            "financeurs": [],
        },
    }


AIDES = [
    _aide("1", ["1"], nom="Nationale subvention", effectif="2,3,4,5",
          natures=["Subvention"], couverture_geo="2"),
    _aide("2", ["50007"], nom="R&eacute;gionale pr&ecirc;t innovation",
          objet="Soutien &agrave; l'<b>innovation</b>", effectif="3,4",
          natures=["Prêt", "Avance récupérable"]),
    _aide("3", ["12550"], nom="Communale Toulouse", effectif=""),
    _aide("4", ["999"], nom="Communale Paris", effectif="2"),
    _aide("5", ["1"], nom="AAP à échéance", effectif="2,3",
          date_fin="2026-09-30 00:00:00", natures=["Subvention"]),
    _aide("6", ["1"], nom="Supprimée", status="0"),
    _aide("7", ["55119"], nom="Arrondissement Marseille 2e"),
]


@pytest.fixture()
def client():
    c = AidesClient()
    c._fetch = lambda name: (AIDES if name == "aides.json" else TERRITOIRES)
    return c


def test_geo_matches_commune_and_ancestors(client):
    r = client.search(insee="31555")
    assert {i["id"] for i in r["items"]} == {"1", "2", "3", "5"}  # pas Paris, pas la supprimée
    assert r["funnel"] == {"base": 6, "geo": 4}


def test_geo_by_code_postal(client):
    r = client.search(code_postal="75003")
    assert {i["id"] for i in r["items"]} == {"1", "4", "5"}


def test_plm_commune_mere_resolves_all_arrondissements(client):
    # 13055 absent du référentiel → union des arrondissements 132xx
    r = client.search(insee="13055")
    assert {i["id"] for i in r["items"]} == {"1", "5", "7"}


def test_unknown_commune_raises(client):
    with pytest.raises(ValueError, match="99999"):
        client.search(insee="99999")
    with pytest.raises(ValueError, match="INSEE"):
        client.search(code_postal="00000")


def test_effectif_filter_keeps_empty_field(client):
    r = client.search(insee="31555", effectif=5)  # <10 → code 2
    # l'aide 2 (codes 3,4) tombe ; l'aide 3 (champ vide) reste
    assert {i["id"] for i in r["items"]} == {"1", "3", "5"}
    assert r["funnel"]["effectif"] == 3


def test_effectif_code_ranges():
    assert _codes_for_effectif(0) == {"1", "2"}
    assert _codes_for_effectif(9) == {"2"}
    assert _codes_for_effectif(49) == {"3"}
    assert _codes_for_effectif(249) == {"4"}
    assert _codes_for_effectif(1000) == {"5", "6"}


def test_nature_and_lexical_and_echeance(client):
    assert [i["id"] for i in client.search(nature="prêt")["items"]] == ["2"]
    assert [i["id"] for i in client.search(q="innovation")["items"]] == ["2"]
    r = client.search(echeance_avant="2026-12-31")
    assert [i["id"] for i in r["items"]] == ["5"]
    assert client.search(echeance_avant="2026-01-01")["count"] == 0


def test_compact_unescapes_html_and_levels(client):
    r = client.search(q="innovation")
    item = r["items"][0]
    assert item["nom"] == "Régionale prêt innovation"
    assert "<b>" not in item["objet"] and "innovation" in item["objet"]
    assert item["niveau"] == "territoriale"
    assert client.search(insee="31555")["items"][0]["niveau"] in {"nationale", "territoriale"}


def test_get_returns_raw(client):
    client.search()  # force le load
    raw = client.get("2", raw=True)
    assert raw["aid_nom"].startswith("R&eacute;")   # brut = entités HTML intactes
    assert "cache_indexation" in raw
    assert client.get("404") is None


def test_get_default_is_cleaned(client):
    client.search()
    a = client.get("2")                              # défaut = detail() (nettoyé)
    assert a["aid_nom"] == "Régionale prêt innovation"   # entités décodées
    assert a["natures"] == ["Prêt", "Avance récupérable"]  # extrait de cache_indexation
    assert "cache_indexation" not in a               # bruit de jointure retiré
    assert a["niveau"] == "territoriale"


def test_helpers():
    assert _text("A&nbsp;<b>B</b>  C") == "A B C"
    assert _date_fin({"date_fin": "2026-09-30 00:00:00"}) == "2026-09-30"
    assert _date_fin({"date_fin": "0000-00-00 00:00:00"}) is None
    assert _date_fin({}) is None
