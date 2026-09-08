"""Rapprochement site → établissement par la géométrie.

Offline : `rapprocher` est une fonction pure, la projection est du `math`.
"""
from france_opendata.geo import lambert93
from france_opendata.resolution import classer_par_distance, rapprocher

# Un point de référence et des établissements posés autour, en Lambert 93.
LAT, LON = 44.8935, -0.5085          # Bassens, quai Alfred de Vial
X0, Y0 = lambert93(LAT, LON)


def _etab(nom, dx=0.0, dy=0.0, avec_position=True):
    e = {"nom": nom}
    if avec_position:
        e["lambert_x"], e["lambert_y"] = X0 + dx, Y0 + dy
    return e


def test_la_projection_colle_a_lorigine_legale():
    """(46.5N, 3E) est l'origine de Lambert 93 par définition."""
    x, y = lambert93(46.5, 3.0)
    assert abs(x - 700000) < 0.01 and abs(y - 6600000) < 0.01


def test_un_seul_candidat_dans_le_rayon_tranche():
    r = rapprocher(LAT, LON, [_etab("SAIPOL", 40), _etab("Loin", 5000)])
    assert r["statut"] == "résolu"
    assert r["match"]["nom"] == "SAIPOL" and r["distance_m"] < 50
    assert r["candidats_dans_rayon"] == 1


def test_deux_candidats_serres_sortent_ambigus_et_ne_tranchent_pas():
    """Le cas normal d'une zone industrielle. Le rapprochement par mots de voie
    tranchait toujours ici — c'est exactement ce qu'on refuse de faire."""
    r = rapprocher(LAT, LON, [_etab("Exploitant A", 40), _etab("Exploitant B", 90)])
    assert r["statut"] == "ambigu"
    assert r["match"] is not None            # on rend le plus proche…
    assert r["ecart_au_second_m"] < 150      # …mais on dit qu'il ne prouve rien


def test_rien_dans_le_rayon_ne_se_rapproche_pas():
    r = rapprocher(LAT, LON, [_etab("Ailleurs", 4000)])
    assert r["statut"] == "non résolu" and r["match"] is None
    assert r["plus_proche_hors_rayon_m"] > 3000   # on dit à quelle distance il était


def test_un_etablissement_sans_coordonnees_nest_pas_loin_il_est_inconnu():
    r = rapprocher(LAT, LON, [_etab("Sans position", avec_position=False), _etab("Ici", 20)])
    assert r["statut"] == "résolu" and r["match"]["nom"] == "Ici"
    assert r["sans_position"] == 1           # compté, pas classé au fond
    classes, sans = classer_par_distance(LAT, LON, [_etab("X", avec_position=False)])
    assert classes == [] and len(sans) == 1


def test_des_coordonnees_en_chaine_passent_comme_le_parquet_les_rend():
    e = _etab("Chaîne", 30); e["lambert_x"], e["lambert_y"] = str(e["lambert_x"]), str(e["lambert_y"])
    assert rapprocher(LAT, LON, [e])["statut"] == "résolu"


def test_les_deux_signaux_daccord_valent_mieux_quun_seul():
    from france_opendata.resolution import combiner
    geo = rapprocher(LAT, LON, [{**_etab("A", 20), "siren": "111111111"}])
    r = combiner(geo, "111111111")
    assert r["statut"] == "résolu" and r["confiance"] == "accord" and r["siren"] == "111111111"


def test_deux_signaux_qui_se_contredisent_remontent_le_conflit():
    """Deux méthodes indépendantes qui divergent : le pire serait d'en choisir une."""
    from france_opendata.resolution import combiner
    geo = rapprocher(LAT, LON, [{**_etab("A", 20), "siren": "111111111"}])
    r = combiner(geo, "222222222")
    assert r["confiance"] == "conflit" and r["statut"] == "ambigu"
    assert r["siren"] is None                     # on ne tranche pas à pile ou face
    assert r["conflit"] == {"geometrie": "111111111", "lexical": "222222222"}


def test_un_seul_signal_tranche_et_le_dit():
    from france_opendata.resolution import combiner
    geo = rapprocher(LAT, LON, [{**_etab("Loin", 9000), "siren": "111111111"}])
    assert geo["statut"] == "non résolu"
    r = combiner(geo, "222222222")               # seul le lexical a trouvé
    assert r["confiance"] == "signal_unique" and r["siren"] == "222222222"


def test_aucun_signal_reste_non_resolu():
    from france_opendata.resolution import combiner
    geo = rapprocher(LAT, LON, [{**_etab("Loin", 9000), "siren": "111111111"}])
    r = combiner(geo, None)
    assert r["confiance"] == "aucun" and r["statut"] == "non résolu" and r["siren"] is None
