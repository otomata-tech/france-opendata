"""Retry/backoff sur flakiness AMONT INSEE Mélodi (#194-195).

L'API Mélodi renvoyait par intermittence des 5xx / resets sur tous les blocs à la
fois (non corrélés à la charge) → un retry les absorbe. Un 4xx (territoire/requête
invalide) n'est PAS retryé. Seams réseau mockés (pas de dépendance à api.insee.fr).
"""
import pytest
import requests

import france_opendata.insee_melodi as m
from france_opendata.insee_melodi import InseeMelodiClient


class _Resp:
    def __init__(self, code, obs=None):
        self.status_code = code
        self._obs = obs or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return {"observations": self._obs}


def _client(monkeypatch, responses):
    monkeypatch.setattr(m, "_RETRY_BACKOFF", (0, 0, 0))  # pas d'attente en test
    c = InseeMelodiClient()
    it = iter(responses)
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r

    c._session.get = fake_get
    return c, calls


def test_retry_absorbs_transient_5xx(monkeypatch):
    c, calls = _client(monkeypatch, [_Resp(500), _Resp(503), _Resp(200, [{"x": 1}])])
    assert c._get("DS_X", "69381") == [{"x": 1}]
    assert calls["n"] == 3  # a retenté jusqu'au succès


def test_retry_absorbs_connection_reset(monkeypatch):
    c, calls = _client(monkeypatch, [requests.ConnectionError("reset"),
                                     _Resp(200, [{"y": 2}])])
    assert c._get("DS_X", "69381") == [{"y": 2}]
    assert calls["n"] == 2  # reset amont absorbé par le retry


def test_4xx_not_retried(monkeypatch):
    c, calls = _client(monkeypatch, [_Resp(404)])
    with pytest.raises(requests.HTTPError):
        c._get("DS_X", "69381")
    assert calls["n"] == 1  # requête/territoire invalide → pas de retry


def test_gives_up_after_retries(monkeypatch):
    c, calls = _client(monkeypatch, [_Resp(500)] * 4)
    with pytest.raises(requests.HTTPError):
        c._get("DS_X", "69381")
    assert calls["n"] == 4  # 1 tentative + 3 retries
