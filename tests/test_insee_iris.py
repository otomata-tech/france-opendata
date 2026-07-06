"""INSEE IRIS (parquet bundlé) + résolution de territoire Mélodi (ARM vs COM).

Offline : le client IRIS lit le parquet embarqué dans le package, `_geo_ref` est
une fonction pure. Aucun appel réseau. Nécessite l'extra [stock] (duckdb).
"""
import pytest

from france_opendata.insee_melodi import _geo_ref


def test_geo_ref_arrondissements_municipaux():
    # Paris / Lyon / Marseille = arrondissements municipaux -> ARM-
    assert _geo_ref("13201") == "ARM-13201"   # Marseille 1er
    assert _geo_ref("75112") == "ARM-75112"   # Paris 12e
    assert _geo_ref("69383") == "ARM-69383"   # Lyon 3e
    # communes normales -> COM- (dont chefs-lieux Paris/Lyon/Marseille entiers)
    assert _geo_ref("13055") == "COM-13055"   # Marseille commune
    assert _geo_ref("75056") == "COM-75056"   # Paris commune
    assert _geo_ref("2A004") == "COM-2A004"   # Corse (non numérique) ne casse pas


def test_iris_by_commune_marseille_1er():
    pytest.importorskip("duckdb")
    from france_opendata.insee_iris import InseeIrisClient

    r = InseeIrisClient().by_commune("13201")
    assert r["nb_iris"] == 19            # Marseille 1er est découpé en 19 IRIS
    assert r["totaux"]["pop"] > 30000    # ~39k habitants
    # cohérence : les ménages en appartement ne dépassent pas les résidences principales
    assert r["totaux"]["rp_en_appartement"] <= r["totaux"]["res_principales"]


def test_iris_by_code_and_unknown():
    pytest.importorskip("duckdb")
    from france_opendata.insee_iris import InseeIrisClient

    c = InseeIrisClient()
    row = c.by_iris("132010101")
    assert row is not None
    assert row["com"] == "13201" and row["pop"] > 0
    assert c.by_iris("999999999") is None
