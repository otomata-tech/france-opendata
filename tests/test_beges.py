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


# --- ce que le bilan porte en plus des émissions ---------------------------
# Ajouté le 11/09/2026 : le module ne demandait que les 22 postes et les
# métadonnées. Le même enregistrement porte le responsable du suivi, le périmètre
# consolidé et, dans le poste 2.1, de quoi déduire une consommation électrique.

from france_opendata.beges import (  # noqa: E402
    FE_ELECTRICITE_KGCO2E_KWH,
    _contact,
    _electricite,
    sirens_consolides,
)


def test_un_contact_masque_a_la_source_ne_devient_pas_un_nom():
    """L'ADEME publie le champ et y met « [Masqué] » quand le déclarant refuse la
    diffusion. Rendre ce texte comme un nom fabriquerait un contact inexistant —
    et un refus de diffusion ne se traite pas comme une donnée absente."""
    assert _contact({"responsable_du_suivi": "[Masqué]", "courriel": "[Masqué]"}) is None
    assert _contact({"responsable_du_suivi": "Non communiqué"}) is None
    assert _contact({}) is None


def test_un_contact_declare_sort_avec_sa_fonction():
    """C'est la fonction qui fait la valeur du contact : « responsable énergie »
    est un interlocuteur, un nom seul n'est qu'une ligne de plus."""
    c = _contact({
        "responsable_du_suivi": "Marie Dupont",
        "fonction": "Responsable énergie",
        "courriel": "m.dupont@exemple.fr",
    })
    assert c == {
        "nom": "Marie Dupont",
        "fonction": "Responsable énergie",
        "telephone": None,
        "courriel": "m.dupont@exemple.fr",
    }


def test_le_perimetre_consolide_ne_retient_que_des_siren_entiers():
    """Le champ est une saisie libre. Un nombre à huit chiffres peut être un SIREN
    amputé — mais ici, contrairement au champ numérique `siren_principal`, rien ne
    prouve que le zéro soit tombé à la saisie : on l'écarte au lieu de l'inventer."""
    assert sirens_consolides("552100554 ; 443061841") == ["552100554", "443061841"]
    assert sirens_consolides("95720314") == []
    assert sirens_consolides(None) == []


def test_le_perimetre_consolide_dedoublonne_en_gardant_l_ordre():
    assert sirens_consolides("552100554, 552100554 et 443061841") == [
        "552100554",
        "443061841",
    ]


def test_l_electricite_est_DEDUITE_du_poste_2_1_et_le_dit():
    """100 tCO2e au poste 2.1, divisés par le facteur moyen français, valent
    ~1 923 MWh. Le facteur du déclarant nous est inconnu : la valeur sort donc
    marquée `infere`, avec le facteur employé pour que le calcul soit refaisable."""
    e = _electricite({"emissions_publication_p21": 100.0})
    assert e["mwh_estime"] == round(100.0 / FE_ELECTRICITE_KGCO2E_KWH, 1)
    assert e["certitude"] == "infere"
    assert e["facteur_kgco2e_kwh"] == FE_ELECTRICITE_KGCO2E_KWH
    assert e["tco2e"] == 100.0, "la valeur source reste lisible à côté de la dérivée"


def test_un_poste_2_1_absent_ne_fait_pas_une_consommation_nulle():
    """Le piège du module entier : un poste non déclaré n'est pas un zéro."""
    assert _electricite({}) is None
    assert _electricite({"emissions_publication_p21": None}) is None
    assert _electricite({"emissions_publication_p21": 0}) is None
