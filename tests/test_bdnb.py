"""BDNB — le propriétaire personne morale, et ce que son absence veut dire.

Offline : `_signal` et `_autres_proprietaires` sont des fonctions pures.

L'enjeu du module tient en une phrase : c'est la seule source publique qui relie un
bâtiment à un SIREN. Tout le reste du connecteur existe pour que cette relation ne
se lise pas plus largement qu'elle ne vaut.
"""
from france_opendata.bdnb import LIMIT_MAX, PAGE_MAX, _autres_proprietaires, _signal, seuil_emprise


def _row(**kw):
    base = {
        "batiment_groupe_id": "bdnb-bg-67E1-SCC3-GN3V",
        "code_commune_insee": "59526",
        "libelle_commune_insee": "Saint-Amand-les-Eaux",
        "siren": "484425277",
        "l_siren": ["484425277"],
        "l_denomination_proprietaire": ["MBRE"],
        "nb_locaux_open": 1,
        "dans_majic_pm": True,
        "surface_emprise_sol": 2661,
        "conso_pro_dle_elec_2020": 223243.34,
    }
    base.update(kw)
    return base


def test_le_batiment_porte_le_siren_de_son_proprietaire():
    """La raison d'être du module : un lieu, une personne morale, sans rapprochement
    d'adresse — la relation est exacte, elle vient du foncier."""
    s = _signal(_row())
    assert s["proprietaire"]["siren"] == "484425277"
    assert s["proprietaire"]["denomination"] == "MBRE"
    assert s["ref_key"] == s["batiment_groupe_id"]


def test_la_consommation_porte_son_unite_dans_le_nom():
    """La source publie des kWh. Les rendre sous un nom neutre inviterait à les lire
    comme des MWh — un facteur mille, indétectable en aval."""
    s = _signal(_row())
    assert s["energie"]["conso_pro_elec_kwh"] == 223243.34
    assert s["energie"]["millesime_conso"] == 2020


def test_une_mesure_absente_reste_absente():
    """Un bâtiment sans conso déclarée n'est pas un bâtiment qui ne consomme rien."""
    s = _signal(_row(conso_pro_dle_elec_2020=None, surface_emprise_sol=None))
    assert s["energie"]["conso_pro_elec_kwh"] is None
    assert s["bati"]["emprise_m2"] is None


def test_les_coproprietaires_sortent_sans_repeter_le_principal():
    autres = _autres_proprietaires(
        _row(l_siren=["484425277", "451938930"],
             l_denomination_proprietaire=["MBRE", "LES TROIS FRERES"])
    )
    assert autres == [{"siren": "451938930", "denomination": "LES TROIS FRERES"}]


def test_deux_listes_de_longueurs_differentes_ne_se_realignent_pas():
    """`l_siren` et `l_denomination_proprietaire` sont parallèles. Si l'une est plus
    courte, prendre le nom de la ligne d'à côté attribuerait un bâtiment à la mauvaise
    société — une erreur qui se propage sans jamais se voir."""
    autres = _autres_proprietaires(
        _row(l_siren=["484425277", "451938930", "552100554"],
             l_denomination_proprietaire=["MBRE"])
    )
    assert [a["denomination"] for a in autres] == [None, None]
    assert [a["siren"] for a in autres] == ["451938930", "552100554"]


def test_une_ligne_sans_identifiant_de_batiment_ne_fait_pas_un_signal():
    assert _signal({"siren": "484425277"}) is None


def test_le_plafond_de_l_api_est_dit_par_une_constante():
    """`limit=100` rend 10 lignes (mesuré le 11/09/2026) : le nombre est une propriété
    de la source, pas un réglage — d'où la pagination interne."""
    assert PAGE_MAX == 10
    assert LIMIT_MAX >= PAGE_MAX


def test_le_seuil_d_emprise_part_en_entier():
    """Mesuré en production le 11/09/2026 : la route répondait 400 dès qu'on filtrait
    par emprise. La colonne est entière, PostgREST refuse `gte.1000.0` — et le
    service, typé en `float`, n'envoie jamais autre chose. Depuis un poste, un
    appel en `int` passait : le défaut ne se voyait qu'une fois déployé."""
    assert seuil_emprise(1000.0) == 1000
    assert seuil_emprise(1000) == 1000
    assert seuil_emprise(1000.5) == 1001, "arrondi au-dessus : la sémantique de ≥ tient"
