"""Le bloc `finances` ne doit jamais se lire comme des euros sûrs.

L'amont (API Recherche Entreprises) sert `{ca, resultat_net}` sans unité et code
l'absence par un 0. Les deux manques sont invisibles : le nombre reste plausible pour
une petite structure, donc un consommateur affiche « 392 287 € » pour une banque
régionale à 392 M€ sans que rien ne cloche.

Valeurs de référence relevées sur l'API le 12/08/2026 — elles ancrent les tests sur
du réel, pas sur des cas inventés.
"""
from __future__ import annotations

import pytest

from france_opendata import alertes, finances


def _last(annotated):
    return annotated[sorted(annotated)[-1]]


# --- ce qui n'est pas une donnée est retiré ------------------------------------

def test_zero_is_absence_not_a_null_turnover():
    """NORAUTO FRANCE : `ca: 0` chez l'amont, 974 718 176 € au dépôt 2023.

    Rendre le 0 tel quel fait afficher « 0 € » pour une entreprise qui pèse presque
    un milliard — l'erreur la plus grave du lot, parce qu'elle est muette."""
    ann, avert = finances.annotate(
        {"2024": {"ca": 0, "resultat_net": 37748283}}, "52")
    assert _last(ann)["ca"] is None
    assert _last(ann)["alerte"] == ["non_declare"]
    assert avert


def test_negative_turnover_is_unreadable_but_the_raw_value_survives():
    """SAFRAN NACELLES : ca = -1 002 180 648. On ne sait pas le lire, mais on ne
    jette pas ce que l'amont a dit — l'appelant doit pouvoir constater."""
    ann, _ = finances.annotate(
        {"2024": {"ca": -1002180648, "resultat_net": 36026000}}, "51")
    assert _last(ann)["ca"] is None
    assert _last(ann)["ca_valeur_amont"] == -1002180648
    assert _last(ann)["alerte"] == ["valeur_aberrante"]


# --- ce qui est réel mais illisible est marqué, jamais corrigé -----------------

def test_implausible_turnover_keeps_its_value_and_shows_the_ratio():
    """BANQUE POPULAIRE VAL DE FRANCE : 392 287 pour 2 000-4 999 salariés.

    Le montant est réel (c'est un dépôt en milliers), donc on le garde : convertir
    serait deviner, et une conversion fausse est indétectable en aval. On rend le
    ratio qui a déclenché l'alerte pour que l'appelant juge lui-même."""
    ann, avert = finances.annotate(
        {"2024": {"ca": 392287, "resultat_net": 82684}}, "51")
    assert _last(ann)["ca"] == 392287, "on ne convertit RIEN"
    assert _last(ann)["ca_par_salarie"] == 196
    assert _last(ann)["alerte"] == ["invraisemblable"]
    assert avert


def test_the_alert_never_asserts_the_cause():
    """L'étiquette dit « invraisemblable », pas « en milliers ».

    Mesuré sur 311 entreprises de 50+ salariés : les cas marqués étaient une
    association vivant de subventions et une holding portant les salariés d'un
    groupe — pas des erreurs d'unité. Affirmer la cause serait l'inventer."""
    assert "unite_suspecte" not in finances.AVERTISSEMENT
    for cause in ("milliers", "subventions", "holding"):
        assert cause in finances.AVERTISSEMENT, (
            f"l'avertissement doit citer « {cause} » comme cause POSSIBLE")
    assert "NON établie" in finances.AVERTISSEMENT


# --- une fiche saine ne doit pas être alourdie --------------------------------

@pytest.mark.parametrize("ca,tranche,label", [
    (5570764860, "53", "Michelin : 557 076 €/salarié"),
    (733914, "NN", "petite boulangerie, effectif non renseigné"),
    (84937663, "12", "ALIAPUR, 20-49 salariés"),
])
def test_healthy_filings_pass_through_untouched(ca, tranche, label):
    src = {"2024": {"ca": ca, "resultat_net": 1}}
    ann, avert = finances.annotate(src, tranche)
    assert ann == src, label
    assert avert is None, f"pas d'avertissement sur une fiche saine ({label})"


def test_unknown_headcount_disables_the_plausibility_check():
    """Sans effectif, le ratio n'existe pas : on se tait plutôt que de supposer.

    La moitié des établissements n'ont pas de tranche renseignée — inventer un
    plancher ferait des faux positifs en masse."""
    ann, avert = finances.annotate({"2024": {"ca": 1}}, None)
    assert ann == {"2024": {"ca": 1}}
    assert avert is None


# --- robustesse de forme -------------------------------------------------------

@pytest.mark.parametrize("bloc", [None, {}, "pas un dict", []])
def test_absent_or_malformed_block_is_returned_as_is(bloc):
    assert finances.annotate(bloc, "51") == (bloc, None)


def test_year_entries_that_are_not_blocks_survive():
    """L'amont peut changer de forme ; on ne casse pas la fiche pour autant."""
    ann, avert = finances.annotate({"2024": "inattendu"}, "51")
    assert ann == {"2024": "inattendu"}
    assert avert is None


def test_the_source_block_is_not_mutated():
    """`annotate` travaille sur une copie : l'appelant garde le brut de l'amont."""
    src = {"2024": {"ca": 0, "resultat_net": 5}}
    finances.annotate(src, "51")
    assert src == {"2024": {"ca": 0, "resultat_net": 5}}


def test_every_year_is_examined_not_only_the_latest():
    """Robustesse : chaque année est jugée pour elle-même, pas la dernière pour toutes.

    ⚠️ Aujourd'hui l'amont ne sert QU'UN exercice par fiche — mesuré le 12/08/2026
    sur 483 entreprises, dont 382 portant un bloc `finances` : 100 % à un seul
    exercice (l'année varie de 2016 à 2025, c'est le dernier dépôt disponible).
    Ce test couvre donc une forme que l'amont ne produit pas encore, et c'est
    délibéré : le jour où il en sert deux, une entreprise saine une année et
    illisible la suivante ne doit pas contaminer l'autre. Le cas existe déjà dans
    les DÉPÔTS (Michelin : euros jusqu'en 2018, milliers ensuite) — c'est la liasse
    INPI qui les expose, pas ce bloc-ci."""
    ann, avert = finances.annotate(
        {"2018": {"ca": 5500000000}, "2024": {"ca": 5513153}}, "53")
    assert "alerte" not in ann["2018"]
    assert ann["2024"]["alerte"] == ["invraisemblable"]
    assert avert


# --- le filtre ----------------------------------------------------------------

def test_the_filter_warning_states_what_was_measured():
    """`ca_min`/`ca_max` filtrent en amont sur ce même nombre : l'avertissement doit
    porter le constat, pas une précaution vague."""
    txt = finances.FILTRE_CA_AVERTISSEMENT
    assert "ca_max=400000" in txt and "12" in txt
    assert "tranche_effectif_salarie" in txt, "il faut dire par quoi remplacer"


# --- câblage : l'annotation atteint la fiche ----------------------------------

def test_annotate_company_wires_the_block_and_the_warning():
    """`annotate_company` est le point d'application côté client — s'il rate, tous
    les consommateurs de la lib reçoivent le brut sans le savoir."""
    fiche = finances.annotate_company({
        "siren": "480470152", "tranche_effectif_salarie": "52",
        "finances": {"2024": {"ca": 0, "resultat_net": 37748283}},
    })
    assert fiche["finances"]["2024"]["ca"] is None
    assert fiche["finances"]["2024"]["alerte"] == ["non_declare"]
    assert fiche["finances_avertissement"]


def test_annotate_company_leaves_a_healthy_or_financeless_record_alone():
    for fiche in ({"siren": "1"}, {"siren": "1", "finances": None},
                  {"siren": "1", "tranche_effectif_salarie": "53",
                   "finances": {"2024": {"ca": 5570764860}}}):
        assert "finances_avertissement" not in finances.annotate_company(fiche)


def test_emitted_codes_belong_to_the_closed_vocabulary():
    """Le vocabulaire est un CONTRAT : un code émis hors énumération ne peut pas
    être typé par un consommateur, et casserait son exhaustivité."""
    ann, _ = finances.annotate({
        "a": {"ca": 0}, "b": {"ca": -1}, "c": {"ca": 392287},
    }, "51")
    emis = {c for e in ann.values() for c in e.get("alerte", [])}
    assert emis == {"non_declare", "valeur_aberrante", "invraisemblable"}
    assert emis <= set(alertes.CODES)
