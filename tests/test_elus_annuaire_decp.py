"""Élus, annuaire de l'administration, marchés attribués — les pièges de chaque source.

Offline : on ne teste que des fonctions pures. Chaque test porte un défaut MESURÉ
le 11/09/2026 sur la donnée réelle.
"""
from france_opendata.decp import _titulaires, normaliser_identifiant
from france_opendata.elus import _elu
from france_opendata.lannuaire import _responsables, _signal, decoder_json


# --- élus -------------------------------------------------------------------

def test_un_elu_sort_sans_sa_date_de_naissance_ni_son_sexe():
    """Le fichier source les porte ; ils n'ont aucun usage en prospection et ne
    feraient qu'étendre la surface de données personnelles. Ils sont écartés à la
    lecture — un test le garde, pour que personne ne les réintroduise par mégarde."""
    e = _elu({"Nom de l'élu": "DUPONT", "Prénom de l'élu": "Camille",
              "Date de naissance": "1970-01-01", "Code sexe": "F",
              "Code de la commune": "99999"}, "Maire")
    assert e["nom"] == "DUPONT"
    assert "date_naissance" not in e and "Date de naissance" not in e
    assert "sexe" not in e and "Code sexe" not in e


def test_un_president_d_epci_prend_la_commune_de_rattachement():
    """Un EPCI n'a pas de commune propre : le fichier donne celle de rattachement."""
    e = _elu({"Nom de l'élu": "MARTIN", "Code de la commune de rattachement": "99998",
              "Libellé de la commune de rattachement": "Commune-Exemple"}, "Président d'EPCI")
    assert e["code_commune"] == "99998"


# --- annuaire DILA ----------------------------------------------------------

def test_un_champ_json_serialise_est_decode():
    """`affectation_personne` et consorts arrivent en CHAÎNE JSON : lus tels quels,
    ils sortent comme un texte que personne n'exploite."""
    val, ko = decoder_json('[{"valeur": "02 38 34 02 22"}]')
    assert val == [{"valeur": "02 38 34 02 22"}] and ko is False


def test_un_json_corrompu_est_signale_pas_avale():
    """Rendre None sans rien dire confondrait une donnée corrompue à la source avec
    une donnée absente."""
    assert decoder_json("[{pas du json") == (None, True)
    s = _signal({"nom": "Mairie", "affectation_personne": "[{cassé"})
    assert s["champs_illisibles"] == ["affectation_personne"]


def test_le_responsable_sort_avec_sa_fonction_et_son_courriel():
    """Le courriel d'une personne arrive comme le téléphone : une liste de
    `{libelle, valeur}` (mesuré le 11/09/2026 sur Lille : 35 listes vides, 2 remplies,
    aucune chaîne). Lu tel quel, il sortait en objets bruts."""
    r = _responsables([
        {"personne": {"nom": "DURAND", "prenom": "Alex",
                      "adresse_courriel": [{"libelle": "", "valeur": "direction@service.example"}]},
         "fonction": "Directrice départementale"},
        {"personne": {"nom": "MARTIN", "adresse_courriel": []}, "fonction": "Adjoint"},
    ])
    assert r[0]["fonction"] == "Directrice départementale"
    assert r[0]["courriel"] == ["direction@service.example"]
    assert r[1]["courriel"] == []


# --- DECP ---------------------------------------------------------------------

def test_le_siret_stocke_en_nombre_retrouve_son_zero_de_tete():
    """Mesuré : `5780122700059` pour `05780122700059`. Une jointure sur str(valeur)
    rate tous les titulaires dont le SIRET commence par zéro."""
    assert normaliser_identifiant(5780122700059, "SIRET") == "05780122700059"
    assert normaliser_identifiant(79141266100021, "SIRET") == "79141266100021"


def test_un_identifiant_qui_n_est_pas_un_siret_ne_se_complete_pas():
    """Un numéro de TVA ou un identifiant étranger n'a pas de longueur fixe connue :
    le compléter fabriquerait un identifiant qui n'existe pas."""
    assert normaliser_identifiant("FR12345678901", "TVA") == "FR12345678901"
    assert normaliser_identifiant(12345, "HORS-UE") == "12345"


def test_le_titulaire_principal_n_a_pas_de_denomination_dans_le_jeu():
    """Les colonnes existent pour les rangs 2 et 3, pas pour le rang 1 : `None` n'est
    pas un accident, c'est la forme de la source."""
    t = _titulaires({"titulaire_id_1": 79141266100021, "titulaire_typeidentifiant_1": "SIRET",
                     "titulaire_id_2": 38012986600025, "titulaire_typeidentifiant_2": "SIRET",
                     "titulaire_denominationsociale_2": "COTRAITANT SA"})
    assert t[0]["denomination"] is None
    assert t[1]["denomination"] == "COTRAITANT SA"
