"""IREP — « < seuil » n'est ni un zéro ni une absence.

Offline : `quantite_declaree` et `_etablissement` sont des fonctions pures.

Le fait qui commande tout le module : sur le millésime 2024, **56 848 lignes sur
64 045 portent la chaîne « < seuil »** au lieu d'un nombre. Un `float()` y plante,
et y substituer 0 invente une mesure sur 89 % du registre.
"""
from france_opendata.irep import CO2_FOSSILE, CO2_TOTAL, _etablissement, quantite_declaree


def test_sous_le_seuil_nest_pas_zero_et_se_dit():
    """L'exploitant a déclaré une émission INFÉRIEURE au seuil : il émet, mais sous
    la borne. Rendre 0 effacerait l'émission, rendre None sans le dire la confondrait
    avec une non-déclaration."""
    assert quantite_declaree("< seuil") == (None, True)
    assert quantite_declaree("  < seuil ") == (None, True)


def test_une_quantite_chiffree_sort_en_nombre():
    assert quantite_declaree("5995000000") == (5995000000.0, False)
    assert quantite_declaree("1234,5") == (1234.5, False), "séparateur décimal français"


def test_une_absence_nest_pas_un_passage_sous_le_seuil():
    """Deux faits distincts : rien de déclaré, ou déclaré sous la borne."""
    assert quantite_declaree("") == (None, False)
    assert quantite_declaree(None) == (None, False)


def test_une_valeur_illisible_ne_devient_pas_un_nombre():
    assert quantite_declaree("n/c") == (None, False)


def test_le_code_departement_perd_son_espace_de_fin():
    """Le jeu source écrit « 01 » : sans nettoyage, aucune comparaison ne matche."""
    e = _etablissement({"identifiant": "x", "code_departement": "01 ", "code_insee": "01004"})
    assert e["code_departement"] == "01"


def test_le_siret_de_l_etablissement_est_rendu():
    """C'est tout l'intérêt face à BEGES : un bilan GES porte sur l'organisation
    entière, l'IREP désigne l'établissement — donc l'adresse où se présenter."""
    e = _etablissement({"identifiant": "0007000849", "numero_siret": "56209442500468"})
    assert e["siret"] == "56209442500468"


def test_les_deux_libelles_de_co2_sont_distincts():
    """« total » somme la biomasse et le fossile : les confondre gonfle le chiffre
    d'un site qui brûle du bois."""
    assert CO2_FOSSILE != CO2_TOTAL
    assert "non biomasse" in CO2_FOSSILE
