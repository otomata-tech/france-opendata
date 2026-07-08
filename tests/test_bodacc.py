"""BODACC — cœur déterministe (offline, sans réseau).

Verrouille les deux corrections de 0.29 : l'alias `famille` (le filtre
"procedure_collective" ne matchait AUCUNE ligne avant → doit devenir "collective")
et la dérivation de la synthèse d'agrégation.
"""
from france_opendata.bodacc import BodaccClient, _famille_ods, _siren_of


def test_famille_alias_maps_to_ods_value():
    assert _famille_ods("procedure_collective") == "collective"
    assert _famille_ods("procedures_collectives") == "collective"
    assert _famille_ods("collective") == "collective"  # valeur canonique inchangée
    assert _famille_ods("creation") == "creation"       # non-alias passe tel quel
    assert _famille_ods(None) is None


def test_siren_extraction_from_registre_list():
    # registre ODS = ['791195415', '791 195 415'] → le SIREN = l'élément 9 chiffres
    assert _siren_of(["791195415", "791 195 415"]) == "791195415"
    assert _siren_of("418001897") == "418001897"
    assert _siren_of([]) is None
    assert _siren_of(None) is None


def test_synthese_counts_are_deterministic():
    sirens = ["111111111", "222222222", "333333333"]
    annonces = [
        {"siren": "111111111", "famille": "Procédures collectives",
         "type_avis": "Avis initial", "jugement_nature": "Jugement d'ouverture de liquidation judiciaire",
         "jugement_famille": "Extrait de jugement"},
        {"siren": "111111111", "famille": "Procédures collectives",
         "type_avis": "Avis initial", "jugement_nature": "Jugement de clôture pour insuffisance d'actif",
         "jugement_famille": "Extrait de jugement"},
        {"siren": "222222222", "famille": "Procédures collectives",
         "type_avis": "Avis rectificatif", "jugement_nature": "Jugement d'ouverture de liquidation judiciaire",
         "jugement_famille": "Extrait de jugement"},
    ]
    s = BodaccClient._synthese(sirens, annonces)
    assert s["sirens_interroges"] == 3
    assert s["sirens_avec_annonce"] == 2       # 111 et 222 ; 333 sans annonce
    assert s["sirens_sans_annonce"] == 1
    assert s["annonces_total"] == 3
    assert s["par_type_avis"] == {"Avis initial": 2, "Avis rectificatif": 1}
    assert s["par_jugement_nature"]["Jugement d'ouverture de liquidation judiciaire"] == 2


def test_retape_flattens_jugement_json_string():
    raw = {
        "registre": ["791195415", "791 195 415"],
        "dateparution": "2026-07-08",
        "familleavis_lib": "Procédures collectives",
        "typeavis_lib": "Avis initial",
        "tribunal": "TC EVREUX",
        "commercant": "ACME",
        "id": "A1",
        "jugement": '{"famille": "Extrait de jugement", "nature": "Autre jugement et ordonnance", '
                    '"date": "2026-07-03", "complementJugement": "Ouvre la procedure de redressement"}',
    }
    row = BodaccClient._retape(raw)
    assert row["siren"] == "791195415"
    assert row["date_jugement"] == "2026-07-03"
    assert row["texte"] == "Ouvre la procedure de redressement"
    assert row["jugement_famille"] == "Extrait de jugement"
