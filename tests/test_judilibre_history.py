"""Le flux /transactionalhistory de Judilibre nomme l'opération `action`
(`created` | `updated` | `deleted`, relevé sur le flux réel le 22/10/2026).
Une suppression doit sortir comme suppression ; une action inconnue lève."""
from __future__ import annotations

import pytest

from france_opendata.judilibre import JudilibreClient


def _client(transactions):
    c = JudilibreClient.__new__(JudilibreClient)
    c._get = lambda path, **params: {"transactions": transactions}
    c.decision = lambda did: {"id": did, "text": "texte", "jurisdiction": "cc"}
    return c


def test_suppression_produite_comme_suppression():
    sortie = list(_client([{"id": "a1", "action": "deleted", "date": "2026-10-22"},
                           {"id": "b2", "action": "updated", "date": "2026-10-22"}])
                  .iter_history("2026-10-21"))
    assert sortie[0] == {"id": "a1", "_deleted": True}
    assert sortie[1]["id"] == "b2" and not sortie[1].get("_deleted")


def test_action_inconnue_leve():
    with pytest.raises(ValueError, match="action inconnue"):
        list(_client([{"id": "c3", "action": "archived"}]).iter_history("2026-10-21"))
