from unittest.mock import patch, Mock

import pytest
import requests

from france_opendata.georisques import GeorisquesClient


def _resp(content: bytes, status=200, json_=None):
    r = Mock()
    r.content = content
    r.status_code = status
    r.json.return_value = json_
    r.raise_for_status.side_effect = (
        requests.HTTPError("boom") if status >= 400 else None)
    return r


def test_corps_vide_est_pas_de_donnee():
    with patch("france_opendata.georisques.requests.get", return_value=_resp(b"")):
        out = GeorisquesClient().alea_argiles(2.2958, 49.8941)
    assert out["exposition"] is None and out["code_exposition"] is None
    assert out["donnee"] is False and out["note"]


def test_exposition_normale():
    r = _resp(b"{}", json_={"codeExposition": "2", "exposition": "Exposition moyenne"})
    with patch("france_opendata.georisques.requests.get", return_value=r):
        out = GeorisquesClient().alea_argiles(-0.58, 44.84)
    assert out == {"code_exposition": "2", "exposition": "Exposition moyenne", "donnee": True}


def test_erreur_amont_reste_une_erreur():
    with patch("france_opendata.georisques.requests.get", return_value=_resp(b"x", 503)):
        with pytest.raises(requests.HTTPError):
            GeorisquesClient().alea_argiles(1, 1)
