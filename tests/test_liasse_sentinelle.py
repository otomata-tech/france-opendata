"""La sentinelle du parquet dit deux choses ; les confondre efface le CA des géants.

Le parquet « Données financières détaillées » stocke la liasse en INT32. La même
valeur (2 147 483 647) y code « poste absent » et encaisse tout montant qui déborde.
L'ancien filtre les retirait toutes les deux, si bien que Michelin sortait sans
chiffre d'affaires sur 4 exercices — indistinguable d'une entreprise qui n'a jamais
déposé (#10).

Mesures de référence sur les 2 688 lignes à `FL` saturé : 24,4 postes > 100 M€ par
ligne saturée contre 0,37 sur une ligne normale (facteur 66) ; et au grain ligne,
84,5 % portent 10 gros postes ou plus, 13,7 % en portent 3 à 9, 1,5 % en portent
1 à 2, et les 0,3 % restantes (9 lignes) n'en portent aucun — mais saturent 29 fois
en moyenne.
"""
from __future__ import annotations

from france_opendata import alertes, liasse

M = liasse.MISSING


# --- séparer le fait de l'interprétation ---------------------------------------

def test_the_sentinel_leaves_the_readable_postings_alone():
    """Un poste indisponible sort de `liasse` mais ne disparaît pas : il est nommé.

    C'est tout l'objet du module — l'ancien filtre le jetait sans trace."""
    postes, indisponibles = liasse.split({"FL": M, "HN": 1_999_700_000, "AA": 500})
    assert postes == {"HN": 1_999_700_000, "AA": 500}
    assert indisponibles == ["FL"]


def test_the_sentinel_never_reappears_as_a_value():
    """⚠️ Le garde-fou central. Réinjecter la sentinelle ferait sortir un chiffre
    d'affaires de 2 147 483 647 — franc, plausible et entièrement inventé, donc pire
    que le `None` ambigu qu'on corrige. La forme est ADDITIVE, jamais un dé-filtrage."""
    postes, _ = liasse.split({str(i): M for i in range(5)} | {"AA": 42})
    assert M not in postes.values()
    assert postes == {"AA": 42}


def test_a_missing_posting_is_not_an_unavailable_one():
    """`None` = le poste n'est pas au dépôt : rien à signaler, il n'existe pas.
    La sentinelle = le poste EXISTE, sa valeur n'est pas lisible."""
    postes, indisponibles = liasse.split({"FL": None, "HN": M})
    assert postes == {}
    assert indisponibles == ["HN"]


# --- ce qu'on affirme, et ce qu'on infère --------------------------------------

def test_a_corroborated_line_names_the_probable_cause():
    """Michelin : la ligne porte d'autres montants frôlant le plafond sans le
    franchir. Le débordement est alors l'explication de loin la plus probable."""
    postes, indisponibles = liasse.split({"FL": M, "HN": 1_999_700_000})
    assert liasse.alertes(postes, indisponibles) == [
        "valeur_indisponible", "saturation_probable"]


def test_massive_saturation_corroborates_on_its_own():
    """Les 9 lignes (0,3 %) sans aucun gros poste NON saturé : elles saturent
    presque partout, ce qui les trahit autrement. Sans cette seconde branche, ce
    sont les plus grandes entreprises du lot qu'on raterait."""
    postes, indisponibles = liasse.split(
        {f"C{i}": M for i in range(12)} | {"AA": 100})
    assert "saturation_probable" in liasse.alertes(postes, indisponibles)


def test_an_uncorroborated_line_states_the_fact_and_stays_silent_on_the_cause():
    """476 lignes portent une sentinelle sur une ligne par ailleurs modeste, où
    « absent » reste l'explication la plus probable.

    On s'abstient sur la CAUSE — mais surtout PAS sur le FAIT : sans
    `valeur_indisponible`, ces lignes retomberaient dans le défaut d'origine, avec
    la satisfaction d'avoir été prudent."""
    postes, indisponibles = liasse.split({"FL": M, "AA": 5_000})
    assert liasse.alertes(postes, indisponibles) == ["valeur_indisponible"]


def test_a_fully_readable_filing_carries_no_alert():
    """Une fiche saine n'est pas alourdie : la PRÉSENCE des clés est le signal."""
    postes, indisponibles = liasse.split({"FL": 974_718_176})
    assert liasse.alertes(postes, indisponibles) == []
    assert liasse.annotate({"siren": "1"}, postes, indisponibles) == {"siren": "1"}


# --- le contrat ----------------------------------------------------------------

def test_annotate_exposes_both_the_codes_and_the_alert():
    postes, indisponibles = liasse.split({"FL": M, "HN": 1_999_700_000})
    rec = liasse.annotate({"siren": "855200507"}, postes, indisponibles)
    assert rec["postes_indisponibles"] == ["FL"]
    assert rec["alerte"] == ["valeur_indisponible", "saturation_probable"]


def test_emitted_codes_belong_to_the_closed_vocabulary():
    postes, indisponibles = liasse.split({"FL": M, "HN": 1_999_700_000})
    assert set(liasse.alertes(postes, indisponibles)) <= set(alertes.CODES)


def test_each_code_declares_what_it_engages():
    """Une annotation qui surestime sa certitude est le mensonge qu'on élimine, un
    cran plus haut — et une qui la sous-estime tait un fait qu'on possède."""
    assert alertes.certitude("valeur_indisponible") == alertes.PROUVE
    assert alertes.certitude("saturation_probable") == alertes.INFERE
    assert alertes.certitude("non_declare") == alertes.PROUVE
    assert alertes.certitude("invraisemblable") == alertes.INFERE
