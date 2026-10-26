"""DVF — `comparables_by_address` dit quand le géocodage BAN a dégradé l'adresse.

Retour d'usage : « rue des Hirondelles 83320 Carqueiranne » était géocodée « Allée
des Hirondelles » sans qu'aucun champ ne le dise ; « 18 allée des Hirondelles … »,
numéro inconnu de la BAN, se repliait sur le centroïde de la voie, et `distance_m`
se lisait comme mesurée depuis l'adresse exacte.

Réponses BAN à la forme de l'API (propriétés `type`, `housenumber`, `street`/`name`).
Hors réseau : la session HTTP est une doublure.
"""
import pytest

from france_opendata.dvf import DvfClient, ecarts_geocodage


def _ban(label, type_, score, housenumber=None, street=None, name=None):
    props = {"label": label, "type": type_, "score": score, "citycode": "83034"}
    for k, v in (("housenumber", housenumber), ("street", street), ("name", name)):
        if v:
            props[k] = v
    return {"features": [{"geometry": {"coordinates": [6.07, 43.09]}, "properties": props}]}


_VOIE_SEULE = _ban("Allée des Hirondelles 83320 Carqueiranne", "street", 0.71,
                   name="Allée des Hirondelles")
_PORTE = _ban("18 Allée des Hirondelles 83320 Carqueiranne", "housenumber", 0.93,
              housenumber="18", street="Allée des Hirondelles",
              name="18 Allée des Hirondelles")


class _Rep:
    def __init__(self, p):
        self._p = p

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Session:
    def __init__(self, ban):
        self.ban = ban

    def get(self, url, params=None, timeout=None):
        if "api-adresse" in url:
            return _Rep(self.ban)
        return _Rep({"count": 0, "next": None, "features": []})


def _comparables(adresse, ban):
    client = DvfClient()
    client.session = _Session(ban)
    return client.comparables_by_address(adresse, radius_m=300)


def test_le_niveau_et_le_score_sont_rendus():
    res = _comparables("18 allée des Hirondelles 83320 Carqueiranne", _PORTE)
    assert res["geocodage"] == {"type": "housenumber", "score": 0.93}
    assert "avertissement_geocodage" not in res


def test_numero_introuvable_repli_sur_la_voie():
    res = _comparables("18 allée des Hirondelles 83320 Carqueiranne", _VOIE_SEULE)
    assert res["geocodage"]["type"] == "street"
    alerte = res["avertissement_geocodage"]
    assert "Numéro 18 introuvable" in alerte and "distance_m" in alerte


def test_type_de_voie_reinterprete():
    res = _comparables("rue des Hirondelles 83320 Carqueiranne", _VOIE_SEULE)
    assert "« rue » demandé" in res["avertissement_geocodage"]
    assert "Allée des Hirondelles" in res["avertissement_geocodage"]


@pytest.mark.parametrize("adresse", [
    "allée des Hirondelles 83320 Carqueiranne",   # sans numéro : la voie suffit
    "all. des Hirondelles Carqueiranne",           # abréviation du même type
    "des Hirondelles Carqueiranne",                # aucun type dit : rien à comparer
])
def test_pas_d_ecart_quand_rien_ne_diverge(adresse):
    geo = {"type": "street", "voie": "Allée des Hirondelles"}
    assert ecarts_geocodage(adresse, geo) == []


def test_numero_different():
    geo = {"type": "housenumber", "housenumber": "16", "voie": "Allée des Hirondelles"}
    assert ecarts_geocodage("18 allée des Hirondelles", geo) == [
        "Numéro 18 demandé, numéro 16 géocodé."]


def test_indice_de_repetition_compte_comme_le_numero():
    geo = {"type": "housenumber", "housenumber": "18 bis", "voie": "Allée des Hirondelles"}
    assert ecarts_geocodage("18 bis allée des Hirondelles", geo) == []
