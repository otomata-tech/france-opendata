"""Parseur JURI (jurisprudence DILA) — fixtures = XML réels des dumps.

Verrouille les deux variantes de méta : META_JURI (Cass/CA/JADE/constit, avec
FORMATION et ECLI dans le bloc fond-spécifique frère) et META_CNIL (mapping
DATE_TEXTE→date_dec, NATURE_DELIB→solution).
"""
import pathlib

from france_opendata.juri import parse_juri_decision

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_parse_cass():
    row = parse_juri_decision((FIXTURES / "juri_cass.xml").read_bytes())
    assert row["id"] == "JURITEXT000054218357"
    assert row["juridiction"] == "Cour de cassation"
    assert row["numero"] == "P2600690"
    assert row["date_dec"] == "2026-05-29"
    assert row["solution"] == "Cassation partielle"
    assert row["formation"] == "ASSEMBLEE_PLENIERE"  # depuis META_JURI_JUDI
    assert row["ecli"] == "ECLI:FR:CCASS:2026:AP00690"
    assert len(row["texte"]) > 5000


def test_parse_cnil():
    row = parse_juri_decision((FIXTURES / "juri_cnil.xml").read_bytes())
    assert row["id"] == "CNILTEXT000026026270"
    assert row["juridiction"] == "CNIL"
    assert row["numero"] == "2012-138"
    assert row["date_dec"] == "2012-05-03"
    assert row["solution"] == "Autre autorisation"
    assert row["formation"] is None and row["ecli"] is None
    assert row["texte"]


def test_parse_garbage_returns_none():
    assert parse_juri_decision(b"not xml") is None
    assert parse_juri_decision(b"<TEXTE_JURI_JUDI/>") is None
