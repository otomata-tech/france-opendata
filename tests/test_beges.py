"""BEGES — la clé SIREN et les postes non déclarés.

Offline : `normaliser_siren`, `_emissions` et `_signal` sont des fonctions pures.
"""
from france_opendata.beges import CATEGORIES, _emissions, _signal, normaliser_siren


def test_le_siren_stocke_en_nombre_retrouve_son_zero_de_tete():
    """Le jeu stocke le SIREN en NOMBRE : 150 lignes y perdent leur zéro. HEXAOM est
    `95720314` pour `095720314` — une jointure sur str(valeur) les rate toutes."""
    assert normaliser_siren(95720314) == "095720314"
    assert normaliser_siren(55800296) == "055800296"      # Fnac Darty
    assert normaliser_siren("843307646") == "843307646"   # déjà à neuf
    assert normaliser_siren(" 843307646 ") == "843307646"


def test_une_valeur_qui_nest_pas_un_siren_ne_sinvente_pas():
    assert normaliser_siren(None) is None
    assert normaliser_siren("") is None
    assert normaliser_siren("pas un siren") is None
    # au-delà de neuf chiffres on ne devine pas : rendu tel quel, l'appelant voit
    assert normaliser_siren("1234567890") == "1234567890"


def _row(**postes):
    r = {"siren_principal": 843307646, "annee_de_reporting": 2023, "raison_sociale": "ACME"}
    r.update({f"emissions_publication_{k}": v for k, v in postes.items()})
    return r


def test_un_poste_absent_nest_pas_un_zero():
    """Sommer en traitant null comme 0 rend un total plus bas que la réalité, et
    indétectable. On ne somme que ce qui est déclaré, et on dit sur quoi ça porte."""
    e = _emissions(_row(p11=100.0, p12=None, p13=None, p14=None, p15=None))
    cat = e["par_categorie"]["1_directes"]
    assert cat["total"] == 100.0
    assert cat["postes_declares"] == 1 and cat["postes_absents"] == 4


def test_une_categorie_sans_aucun_poste_declare_vaut_none_pas_zero():
    e = _emissions(_row(p11=50.0))
    assert e["par_categorie"]["1_directes"]["total"] == 50.0
    assert e["par_categorie"]["5_produits_vendus"]["total"] is None     # rien déclaré
    assert e["total"] == 50.0


def test_un_bilan_entierement_vide_na_pas_de_total():
    e = _emissions(_row())
    assert e["total"] is None
    assert e["postes_declares"] == 0
    assert e["postes_absents"] == sum(len(p) for p in CATEGORIES.values())


def test_le_total_est_la_somme_des_categories_declarees():
    e = _emissions(_row(p11=10.0, p21=5.0, p41=2.5))
    assert e["total"] == 17.5 and e["unite"] == "tCO2e"


def test_la_cle_porte_le_siren_et_lannee_de_reporting():
    """Un SIREN déclare plusieurs années : la clé doit les distinguer."""
    s = _signal(_row(p11=1.0))
    assert s["ref_key"] == "843307646|2023" and s["siren"] == "843307646"


def test_sans_siren_ou_sans_annee_il_ny_a_pas_de_signal():
    assert _signal({"annee_de_reporting": 2023}) is None
    assert _signal({"siren_principal": 843307646}) is None
