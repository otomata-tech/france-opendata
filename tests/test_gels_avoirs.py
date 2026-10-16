"""Registre des gels d'avoirs : un contrôle ne rassure jamais à tort.

Offline : `gels_avoirs` est pur ; le registre est fabriqué ici, à la forme de la source.
"""
from datetime import datetime, timedelta, timezone

import pytest

from france_opendata import gels_avoirs as g


def _inscrit(id_, nom, prenoms=(), alias=(), naissance=None, nature="Personne physique"):
    detail = [{"TypeChamp": "FONDEMENT_JURIDIQUE",
               "Valeur": [{"FondementJuridique": 1, "FondementJuridiqueLabel": "(UE) 269/2014"}]}]
    if prenoms:
        detail.append({"TypeChamp": "PRENOM", "Valeur": [{"Prenom": p} for p in prenoms]})
    if alias:
        detail.append({"TypeChamp": "ALIAS", "Valeur": [{"Alias": a, "Commentaire": ""} for a in alias]})
    if naissance:
        j, m, a = naissance
        detail.append({"TypeChamp": "DATE_DE_NAISSANCE",
                       "Valeur": [{"Jour": j, "Mois": m, "Annee": a, "Commentaire": ""}]})
    return {"IdRegistre": id_, "Nature": nature, "Nom": nom, "RegistreDetail": detail}


def _registre(n_remplissage=g.INSCRITS_MIN):
    inscrits = [
        _inscrit(1, "SHILKIN", ["Grigory Vladimirovich"], ["Григорий ШИЛКИН"], ("20", "10", "1976")),
        _inscrit(2, "DUPONT-MARTIN", ["Élodie"]),
        _inscrit(3, "SCI LES HORTILLONS", nature="Personne morale"),
    ]
    inscrits += [_inscrit(100 + k, f"REMPLISSAGE{k}") for k in range(n_remplissage)]
    return {"Publications": {"DatePublication": "2026-10-16T11:06:48.0287882+02:00",
                             "PublicationDetail": inscrits}}


@pytest.fixture(scope="module")
def lignes():
    return g.inscrits(_registre())


# --- validation ---------------------------------------------------------------

def test_un_registre_tronque_leve_au_lieu_de_rassurer():
    with pytest.raises(g.RegistreInvalide, match="tronqué"):
        g.valider(_registre(n_remplissage=10))


@pytest.mark.parametrize("lu", [None, {}, {"Publications": {}}, [],
                                {"Publications": {"DatePublication": "2026-10-16", "PublicationDetail": None}}])
def test_un_registre_mal_forme_leve(lu):
    with pytest.raises(g.RegistreInvalide):
        g.valider(lu)


def test_un_inscrit_sans_nom_rend_le_registre_invalide():
    r = _registre()
    r["Publications"]["PublicationDetail"][0]["Nom"] = None
    with pytest.raises(g.RegistreInvalide, match="mal formé"):
        g.valider(r)


def test_la_date_de_publication_garde_son_fuseau():
    # 7 décimales de seconde chez la source : ramenées à six, lisibles dès Python 3.10.
    p = g.publication(_registre())
    assert p.utcoffset() == timedelta(hours=2) and p.microsecond == 28788


def test_une_ligne_porte_ce_qu_un_controle_compare(lignes):
    shilkin = lignes[0]
    assert shilkin == {"id_registre": 1, "nature": "Personne physique", "nom": "SHILKIN",
                       "alias": ["Григорий ШИЛКИН"], "prenoms": ["Grigory Vladimirovich"],
                       "naissances": ["20/10/1976"], "fondements": ["(UE) 269/2014"]}


# --- contrôle -----------------------------------------------------------------

def test_normaliser_ote_accents_casse_et_ponctuation():
    assert g.normaliser("  Dupont-Martin, Élodie ") == "dupont martin elodie"
    assert g.normaliser("Григорий") == ""


@pytest.mark.parametrize("brut, attendu", [
    ("Łukasz Wałęsa", "lukasz walesa"),
    ("Søren Ødegård", "soren odegard"),
    ("Đorđe Đukić", "dorde dukic"),
    ("Straße", "strasse"),
    ("STRAẞE", "strasse"),
    ("Lætitia Cœur", "laetitia coeur"),
    ("Yıldırım", "yildirim"),
    ("İsmail", "ismail"),
    ("Əliyev", "aliyev"),
    ("Þórður", "thordur"),
    ("Ĳsbrand", "ijsbrand"),
])
def test_normaliser_translittere_les_lettres_sans_decomposition(brut, attendu):
    """NFD laisse ł, ø, đ, ß, æ, œ, ı, ə… tels quels ; sans table, ils s'effaceraient."""
    assert g.normaliser(brut) == attendu


@pytest.mark.parametrize("inscrit, cherche", [
    ("WAŁĘSA", "Walesa"), ("ØDEGÅRD", "Odegard"), ("ĐUKIĆ", "Dukic"),
    ("STRAßBERG", "Strassberg"), ("YILDIRIM", "Yıldırım"), ("ƏLIYEV", "Aliyev"),
    ("CŒUR", "Coeur"),
])
def test_un_inscrit_ecrit_en_lettres_speciales_se_trouve_en_ascii(inscrit, cherche):
    lignes = g.inscrits(_registre())
    lignes[2]["nom"] = inscrit
    r = g.controler(lignes, cherche)
    assert r["total"] == 1 and r["correspondances"][0]["nom"] == inscrit


def test_nom_et_prenom_trouvent_l_inscrit(lignes):
    r = g.controler(lignes, "shilkin", "Grigory")
    assert (r["total"], r["nom_et_prenom"], r["nom_seul"]) == (1, 1, 0)
    assert r["correspondances"][0] == {
        "sur": "nom_et_prenom", "id_registre": 1, "nature": "Personne physique", "nom": "SHILKIN",
        "prenoms": "Grigory Vladimirovich", "naissance": "20/10/1976", "fondement": "(UE) 269/2014"}


def test_le_prenom_affine_sans_jamais_ecarter(lignes):
    """Aucun prénom inscrit ne correspond : la correspondance sur le nom reste, signalée."""
    r = g.controler(lignes, "Shilkin", "Jean")
    assert (r["total"], r["nom_et_prenom"], r["nom_seul"]) == (1, 0, 1)
    assert r["correspondances"][0]["sur"] == "nom_seul"


def test_une_correspondance_nom_et_prenom_ne_masque_pas_celles_sur_le_nom_seul():
    """Trois LEFEBVRE : un seul porte le prénom cherché. Tous sont rendus, celui qui
    porte le prénom en tête."""
    lignes = g.inscrits(_registre())
    lignes[3].update(nom="LEFEBVRE", prenoms=["Paul"])
    lignes[4].update(nom="LEFEBVRE", prenoms=["Jean"])
    lignes[5].update(nom="LEFEBVRE", prenoms=[])
    r = g.controler(lignes, "Lefebvre", "Jean")
    assert (r["total"], r["nom_et_prenom"], r["nom_seul"]) == (3, 1, 2)
    assert [(c["sur"], c["prenoms"]) for c in r["correspondances"]] == [
        ("nom_et_prenom", "Jean"), ("nom_seul", "Paul"), ("nom_seul", None)]


def test_sans_prenom_demande_tout_est_sur_le_nom_seul(lignes):
    r = g.controler(lignes, "Shilkin")
    assert (r["total"], r["nom_et_prenom"], r["nom_seul"]) == (1, 0, 1)
    assert r["correspondances"][0]["sur"] == "nom_seul"


def test_tous_les_mots_du_nom_doivent_y_etre(lignes):
    assert g.controler(lignes, "Dupont Martin")["total"] == 1
    assert g.controler(lignes, "dupont-martin", "elodie")["nom_et_prenom"] == 1
    assert g.controler(lignes, "Dupont Durand")["total"] == 0
    # Un mot entier, pas un fragment : « Dupon » n'est pas « Dupont ».
    assert g.controler(lignes, "Dupon")["total"] == 0


def test_une_entite_sans_prenom_se_trouve_par_son_nom(lignes):
    r = g.controler(lignes, "SCI Les Hortillons", "Jean")
    assert (r["total"], r["nom_seul"]) == (1, 1)
    assert r["correspondances"][0]["prenoms"] is None


def test_aucune_correspondance(lignes):
    assert g.controler(lignes, "Durand", "Jean") == {
        "total": 0, "nom_et_prenom": 0, "nom_seul": 0, "correspondances": []}


def test_un_alias_latin_trouve_l_inscrit():
    r = g.inscrits(_registre())
    r[1]["alias"] = ["Elodie Durand"]
    assert g.controler(r, "Durand")["total"] == 1


def test_un_nom_sans_lettre_ne_rapproche_pas_les_alias_non_latins(lignes):
    """« -- » se normalise en rien, comme un alias cyrillique : il les rapprocherait tous."""
    with pytest.raises(ValueError):
        g.controler(lignes, "--")
    with pytest.raises(ValueError):
        g.controler(lignes, "Shilkin", "..")


def test_limit_borne_les_fiches_pas_le_total(lignes):
    r = g.controler(lignes, "remplissage1", limit=1)
    assert r["total"] == 1
    tous = g.inscrits(_registre())
    for ligne in tous[3:8]:
        ligne["alias"] = ["PATRONYME COMMUN"]
    r = g.controler(tous, "patronyme commun", limit=2)
    assert r["total"] == 5 and r["nom_seul"] == 5 and len(r["correspondances"]) == 2


# --- fraîcheur d'une copie gardée ---------------------------------------------

MAINTENANT = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def test_copie_confirmee_recemment_sert_quelle_que_soit_sa_publication():
    g.verifier_fraicheur(MAINTENANT - timedelta(days=30), MAINTENANT - timedelta(hours=1), MAINTENANT)


def test_source_muette_mais_publication_recente_sert():
    g.verifier_fraicheur(MAINTENANT - timedelta(days=6), MAINTENANT - timedelta(days=2), MAINTENANT)


def test_source_muette_et_publication_ancienne_leve():
    with pytest.raises(g.RegistreInvalide, match="trop ancien"):
        g.verifier_fraicheur(MAINTENANT - timedelta(days=8), MAINTENANT - timedelta(days=2), MAINTENANT)
