"""Enedis — la maille SITE contre la maille LIGNE.

Offline : `agreger_par_adresse` et `_signal` sont des fonctions pures, aucun réseau.

Le jeu Enedis publie une ligne par (adresse × division NAF). Confondre les deux mailles
coûte deux choses, testées ici : une clé qui collisionne, et des sites qui disparaissent.
"""
from france_opendata.enedis import _signal, agreger_par_adresse


def _ligne(commune, adresse, naf2, mwh, annee="2024", dept="33", sites=1):
    return {
        "code_commune": commune, "code_departement": dept, "adresse": adresse,
        "annee": annee, "consommation_annuelle_totale_de_ladresse_mwh": mwh,
        "code_secteur_naf2": naf2, "nom_commune": "Ville", "code_grand_secteur": "TERTIAIRE",
        "nombre_de_sites": sites, "code_iris": "330630405",
    }


def test_ref_key_est_partagee_row_key_ne_lest_pas():
    """Le CHU de Bordeaux : deux divisions à la même adresse, même ref_key."""
    a = _signal(_ligne("33318", "1 AVENUE DE MAGELLAN", "86", 20663.0))
    b = _signal(_ligne("33318", "1 AVENUE DE MAGELLAN", "21", 5678.0))
    assert a["ref_key"] == b["ref_key"]      # clé de SITE : partagée, c'est voulu
    assert a["row_key"] != b["row_key"]      # clé de LIGNE : unique, c'est elle qu'on dédoublonne
    assert a["row_key"].endswith("|86")


def test_agregation_somme_les_divisions_dune_meme_adresse():
    lignes = [_signal(_ligne("33318", "1 AVENUE DE MAGELLAN", "86", 20663.0)),
              _signal(_ligne("33318", "1 AVENUE DE MAGELLAN", "21", 5678.0))]
    r = agreger_par_adresse(lignes)
    assert r["total"] == 1 and r["lignes_lues"] == 2
    site = r["signals"][0]
    assert site["mwh"] == 26341.0
    assert site["multi_naf2"] is True
    assert site["naf2_principal"] == "86"          # la division qui pèse le plus
    assert site["naf2_detail"] == {"86": 20663.0, "21": 5678.0}
    assert site["maille"] == "site" and site["reseau"] == "distribution"


def test_le_seuil_apres_agregation_rattrape_le_site_que_le_seuil_par_ligne_rate():
    """Le faux négatif : aucune ligne ne franchit 2000, le site oui."""
    lignes = [_signal(_ligne("33063", "RUE LUCIEN FAURE", "35", 1494.0)),
              _signal(_ligne("33063", "RUE LUCIEN FAURE", "52", 640.0)),
              _signal(_ligne("33063", "RUE LUCIEN FAURE", "10", 194.0))]
    assert all(l["mwh"] < 2000 for l in lignes)
    r = agreger_par_adresse(lignes, min_mwh=2000)
    assert r["total"] == 1                      # seuiller ligne à ligne en rendrait 0
    assert r["signals"][0]["mwh"] == 2328.0


def test_deux_adresses_restent_deux_sites():
    lignes = [_signal(_ligne("33032", "QUAI ALFRED DE VIAL", "10", 55608.0)),
              _signal(_ligne("33032", "BOULEVARD DE L INDUSTRIE", "23", 4374.0))]
    r = agreger_par_adresse(lignes)
    assert r["total"] == 2
    assert [s["mwh"] for s in r["signals"]] == [55608.0, 4374.0]   # trié décroissant


def test_une_ligne_sans_adresse_nest_pas_un_site():
    """Conso réelle mais non localisable : elle ne devient jamais une ligne de résultat."""
    assert _signal(_ligne("33063", None, "85", 4139.6)) is None


def test_liris_retenu_est_celui_de_la_ligne_la_plus_lourde():
    """Deux lignes d'une même adresse peuvent porter deux IRIS : on garde celui qui
    pèse, pas le dernier lu. Comparé avant accumulation — sinon la ligne courante bat
    toujours le total et le « plus lourd » devient « le dernier »."""
    leger = _ligne("33063", "RUE X", "52", 100.0); leger["code_iris"] = "330630999"
    lourd = _ligne("33063", "RUE X", "35", 9000.0); lourd["code_iris"] = "330630111"
    # le lourd d'abord, le léger ensuite : un « dernier lu » retiendrait le mauvais
    r = agreger_par_adresse([_signal(lourd), _signal(leger)])
    assert r["signals"][0]["code_iris"] == "330630111"
    # et dans l'autre ordre, le résultat ne change pas
    r2 = agreger_par_adresse([_signal(leger), _signal(lourd)])
    assert r2["signals"][0]["code_iris"] == "330630111"


def test_la_sortie_est_serialisable_en_json():
    """Le service FOD rend ce dict tel quel : un `set` y ferait un 500 à l'exécution,
    pas à l'import — aucun test de forme ne l'attraperait."""
    import json
    lignes = [_signal(_ligne("33032", "QUAI ALFRED DE VIAL", "10", 55608.0))]
    json.dumps(agreger_par_adresse(lignes))


def test_une_liste_tronquee_le_dit(monkeypatch):
    """`limit` borne le pull et l'export sort par code commune : une coupe perd des
    lignes arbitraires, pas les plus petites. Une liste tronquée qui se tait se lit
    comme une liste complète — c'est ainsi qu'on rate le deuxième plus gros site."""
    from france_opendata import enedis as mod

    c = mod.EnedisClient()
    monkeypatch.setattr(c.ods, "export", lambda *a, **k: [
        _ligne("33003", "RUE A", "20", 100.0), _ligne("33554", "RUE B", "20", 41979.0)])
    r = c.consommation_par_adresse("2024", dept="33", limit=2)
    assert r["tronque"] is True and "min_mwh" in r["avertissement_troncature"]
    r2 = c.consommation_par_adresse("2024", dept="33", limit=-1)
    assert r2["tronque"] is False and r2["avertissement_troncature"] is None
