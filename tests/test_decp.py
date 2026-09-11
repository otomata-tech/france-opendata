"""DECP — deux jeux, un par régime, découpés à la notification.

Offline : une session factice enregistre les requêtes et rend des lignes fabriquées.

Le fait qui commande ce module, mesuré le 11/09/2026 : le jeu `decp-v3-marches-valides`
(arrêté du 22/03/2019) s'ARRÊTE au 8 février 2024. Le lire seul, c'était ne servir aucun
marché récent ; les marchés notifiés depuis 2024 sont dans `decp-2022-marches-valides`.
"""
import pytest

from france_opendata.decp import BASCULE, DecpClient, clause_titulaire


class _Reponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _Session:
    def __init__(self, par_jeu):
        self.par_jeu, self.appels = par_jeu, []

    def get(self, url, params, headers, timeout):
        jeu = url.rsplit("/", 2)[-2]
        self.appels.append((jeu, params["where"]))
        return _Reponse(self.par_jeu[jeu])


def _client(par_jeu) -> DecpClient:
    c = DecpClient()
    c.session = _Session(par_jeu)
    return c


_RECENT = {"total_count": 1, "results": [{"id": "2026-A", "datenotification": "2026-09-09",
                                          "titulaire_id_1": "05780122700059",
                                          "titulaire_typeidentifiant_1": "SIRET"}]}
_ANCIEN = {"total_count": 1, "results": [{"id": "2023-B", "datenotification": "2023-11-22",
                                          "acheteur_nom": "Commune", "titulaire_id_1": 5780122700059,
                                          "titulaire_typeidentifiant_1": "SIRET"}]}
_JEUX = {"decp-2022-marches-valides": _RECENT, "decp-v3-marches-valides": _ANCIEN}


def test_les_deux_regimes_sont_lus_et_chaque_marche_dit_le_sien():
    r = _client(_JEUX).marches(lieu="59")
    assert [(s["ref_key"], s["arrete"]) for s in r["signaux"]] == [("2026-A", "2022"), ("2023-B", "2019")]
    assert r["total"] == 2


def test_chaque_jeu_est_borne_a_son_regime():
    """Le jeu 2022 republie ~99 000 marchés antérieurs à 2024, dont une part se
    retrouve dans l'ancien jeu sous un autre identifiant : sans cette borne, un même
    marché sortirait deux fois."""
    c = _client(_JEUX)
    c.marches(lieu="59")
    clauses = dict(c.session.appels)
    assert f"datenotification >= date'{BASCULE}'" in clauses["decp-2022-marches-valides"]
    assert f"datenotification < date'{BASCULE}'" in clauses["decp-v3-marches-valides"]


def test_une_date_se_cite_entre_apostrophes():
    """`date"2024-01-01"` est une erreur de syntaxe pour la source : `op="awarded"`
    avec `date_from` répondait 400 en production."""
    c = _client(_JEUX)
    c.marches(lieu="59", depuis="2023-06-01")
    assert all("date'2023-06-01'" in w and 'date"' not in w for _, w in c.session.appels)


def test_apres_la_bascule_l_ancien_jeu_n_est_pas_interroge():
    c = _client(_JEUX)
    r = c.marches(lieu="59", depuis="2025-01-01")
    assert [j for j, _ in c.session.appels] == ["decp-2022-marches-valides"]
    assert [s["arrete"] for s in r["signaux"]] == ["2022"]


def test_une_date_mal_formee_est_refusee_avant_tout_appel():
    c = _client(_JEUX)
    with pytest.raises(ValueError, match="AAAA-MM-JJ"):
        c.marches(lieu="59", depuis="01/06/2023")
    assert c.session.appels == []


def test_le_titulaire_est_cherche_a_tous_les_rangs_et_au_bon_type():
    """Rang 1 du jeu 2019 en entier (comparé à une chaîne, il ne matche rien) ; texte
    partout ailleurs, zéro de tête compris. Un co-titulaire de groupement a gagné aussi."""
    assert clause_titulaire("05780122700059", principal_en_nombre=True) == (
        '(titulaire_id_1 = 5780122700059 or titulaire_id_2 = "05780122700059"'
        ' or titulaire_id_3 = "05780122700059")')
    assert clause_titulaire("05780122700059", principal_en_nombre=False).startswith(
        '(titulaire_id_1 = "05780122700059" or')


def test_le_siret_du_titulaire_sort_a_quatorze_chiffres_dans_les_deux_regimes():
    r = _client(_JEUX).marches(titulaire_siret="05780122700059")
    assert {s["titulaires"][0]["identifiant"] for s in r["signaux"]} == {"05780122700059"}
