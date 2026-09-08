"""DPE tertiaire — la position, et ce qu'on refuse d'inventer.

Offline : `_signal` est une fonction pure.
"""
from france_opendata.dpe_tertiaire import _signal


def _row(**kw):
    r = {"numero_dpe": "2333E0123456", "etiquette_dpe": "D", "etiquette_ges": "B",
         "surface_shon": "36000", "secteur_activite": "W : Administrations, banques, bureaux",
         "adresse_ban": "Rue Georges Bonnac 33000 Bordeaux", "code_insee_ban": "33063",
         "coordonnee_cartographique_x_ban": 417000.0, "coordonnee_cartographique_y_ban": 6421000.0}
    r.update(kw)
    return r


def test_les_coordonnees_sortent_sous_les_cles_du_stock_sirene():
    """Le jeu publie déjà du Lambert 93 — vérifié à 0,0 m contre son propre _geopoint.
    Les rendre sous `lambert_x`/`lambert_y` permet à `resolution` de les consommer
    sans conversion ni géocodage intermédiaire."""
    s = _signal(_row())
    assert s["lambert_x"] == 417000.0 and s["lambert_y"] == 6421000.0


def test_un_dpe_non_geocode_na_pas_de_position_plutot_quune_fausse():
    """Le placer au centre de la commune le ferait rapprocher de n'importe quoi."""
    s = _signal(_row(coordonnee_cartographique_x_ban=None, coordonnee_cartographique_y_ban=None,
                     statut_geocodage="adresse non géocodée ban car aucune correspondance trouvée"))
    assert s["lambert_x"] is None and s["lambert_y"] is None
    assert "non géocodée" in s["geocode"]


def test_les_surfaces_en_chaine_deviennent_des_nombres():
    assert _signal(_row())["surface_shon"] == 36000.0
    assert _signal(_row(surface_shon="pas un nombre"))["surface_shon"] is None


def test_une_etiquette_absente_nest_pas_un_g():
    s = _signal(_row(etiquette_dpe=None, etiquette_ges=None))
    assert s["etiquette_dpe"] is None and s["etiquette_ges"] is None


def test_lidentifiant_rnb_est_rendu_quand_il_existe_et_absent_sinon():
    """C'est la clé de jointure exacte vers la base nationale des bâtiments."""
    assert _signal(_row(id_rnb="D2KCFS2WMSHC"))["id_rnb"] == "D2KCFS2WMSHC"
    assert _signal(_row())["id_rnb"] is None


def test_sans_numero_de_dpe_il_ny_a_pas_de_signal():
    assert _signal(_row(numero_dpe=None)) is None
