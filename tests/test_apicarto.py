"""Cadastre IGN via WFS Géoplateforme (PARCELLAIRE_EXPRESS).

Deux niveaux :
- `_geometry_to_wkt` : test pur du swap d'axes GeoJSON [lon,lat] → WKT [lat lon]
  (le seul vrai risque du portage depuis apicarto — offline, déterministe).
- `parcelle_at` : test de non-régression live contre le WFS, sur une parcelle
  connue de Marseille (skip si le réseau/service est indisponible).
"""
import pytest
import requests

from france_opendata.apicarto import ApiCartoClient, _geometry_to_wkt


def test_geometry_to_wkt_swaps_axes():
    # Point : GeoJSON [lon, lat] -> WKT "lat lon"
    assert _geometry_to_wkt({"type": "Point", "coordinates": [5.3698, 43.2965]}) == \
        "POINT(43.2965 5.3698)"
    # Polygon : chaque sommet inversé, anneaux préservés
    poly = {"type": "Polygon", "coordinates": [[[5.0, 43.0], [5.1, 43.0], [5.1, 43.1], [5.0, 43.0]]]}
    assert _geometry_to_wkt(poly) == "POLYGON((43.0 5.0, 43.0 5.1, 43.1 5.1, 43.0 5.0))"
    # coords 3D ([lon, lat, z]) : le z est ignoré
    assert _geometry_to_wkt({"type": "Point", "coordinates": [5.3698, 43.2965, 12.0]}) == \
        "POINT(43.2965 5.3698)"


def test_geometry_to_wkt_rejects_unknown_type():
    with pytest.raises(ValueError):
        _geometry_to_wkt({"type": "GeometryCollection", "coordinates": []})


def test_parcelle_at_marseille_live():
    """Non-régression : la parcelle 132028090C0098 (Marseille) au point connu.

    Mêmes valeurs qu'apicarto avant portage — schéma stable, géométrie [lon,lat].
    """
    try:
        p = ApiCartoClient().parcelle_at(43.2965, 5.3698)
    except requests.RequestException as e:
        pytest.skip(f"WFS Géoplateforme indisponible : {e}")

    assert p is not None
    assert p["idu"] == "132028090C0098"
    assert p["commune"] == "Marseille"
    assert p["code_insee"] == "13055"
    assert p["contenance_m2"] == 892.0
    # sortie en GeoJSON standard [lon, lat] : 1er sommet ~ (5.37, 43.30)
    first = p["geometry"]["coordinates"][0][0][0]
    assert first[0] == pytest.approx(5.37, abs=0.05)   # lon
    assert first[1] == pytest.approx(43.30, abs=0.05)  # lat
