"""DVF — `comparables_by_address` filtre les années CÔTÉ SERVEUR.

L'appel paginait tout l'historique de l'emprise (2014 → courante) puis filtrait les
années en local. En zone dense, 300 m dans un arrondissement parisien portent ~4 900
mutations : la première page mettait 13 à 32 s à venir de l'API Cerema (mesuré le
26/10/2026), une année filtrée ~1 s. Le service rendait « délai dépassé » ou une
erreur interne là où la même zone répond, année par année, en quelques secondes.

Hors réseau : la session HTTP est une doublure qui enregistre ses requêtes.
"""
from datetime import datetime

from france_opendata.dvf import DvfClient


class _Rep:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _feature(annee):
    carre = [[2.3134, 48.8834], [2.3136, 48.8834], [2.3136, 48.8836], [2.3134, 48.8836],
             [2.3134, 48.8834]]
    return {"type": "Feature",
            "geometry": {"type": "MultiPolygon", "coordinates": [[carre]]},
            "properties": {"idopendata": f"m{annee}", "datemut": f"{annee}-06-01",
                           "anneemut": annee, "codtypbien": "121", "sbati": "50",
                           "valeurfonc": "500000", "libtypbien": "UN APPARTEMENT"}}


class _Session:
    def __init__(self, avec_data):
        self.avec_data = avec_data
        self.geomutations = []

    def get(self, url, params=None, timeout=None):
        if "api-adresse" in url:
            return _Rep({"features": [{
                "geometry": {"coordinates": [2.3135, 48.8835]},
                "properties": {"label": "1 Rue Exemple 75017 Paris", "score": 0.97,
                               "type": "housenumber", "citycode": "75117"}}]})
        self.geomutations.append(dict(params or {}))
        annee = (params or {}).get("anneemut")
        feats = [_feature(annee)] if annee in self.avec_data else []
        return _Rep({"count": len(feats), "next": None, "features": feats})


def test_chaque_requete_porte_son_annee():
    courante = datetime.now().year
    client = DvfClient()
    client.session = _Session(avec_data={courante - 1, courante - 2, courante - 4})
    client._reverse_addresses = lambda rows: None
    res = client.comparables_by_address("1 rue exemple 75017 paris", radius_m=300, years=3)
    assert client.session.geomutations, "la zone est interrogée"
    assert all("anneemut" in p for p in client.session.geomutations), \
        "aucune requête sans année : tout l'historique d'une zone dense ne tient pas"
    assert all("in_bbox" in p for p in client.session.geomutations)
    assert sorted(m["annee"] for m in res["mutations"]) == \
        [courante - 4, courante - 2, courante - 1], "trois années AVEC data, comme documenté"
