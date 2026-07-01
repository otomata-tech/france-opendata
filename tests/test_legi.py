"""Parseur LEGI (codes consolidés) — fixture = XML réel du dump DILA.

Verrouille le contrat de version : une ligne = UNE version d'article bornée
[date_debut, date_fin), rattachée à son code via CONTEXTE/TEXTE@cid + TITRE_TXT.
"""
import pathlib

from france_opendata.legi import parse_legi_article

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_parse_article():
    row = parse_legi_article((FIXTURES / "legi_article.xml").read_bytes())
    assert row["id"] == "LEGIARTI000054354526"
    assert row["legitext"] == "LEGITEXT000023086525"
    assert row["titre_texte"] == "Code des transports"
    assert row["num"] == "A5332-103"
    assert row["etat"] == "VIGUEUR"
    assert row["date_debut"] == "2026-07-01"
    assert row["date_fin"] is None  # 2999-01-01 DILA → None
    assert row["texte"].startswith("Le rapport de situation")


def test_parse_garbage_returns_none():
    assert parse_legi_article(b"not xml") is None
    assert parse_legi_article(b"<ARTICLE/>") is None
