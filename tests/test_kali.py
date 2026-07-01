"""Parseurs KALI (conventions collectives) — fixtures = XML réels du dump DILA.

Verrouille le piège du format : l'IDCC vit sur le conteneur (`META_CONTENEUR/NUM`),
le rattachement de l'article vit dans `CONTEXTE/CONTENEUR@cid` (frère de TEXTE,
pas enfant — l'erreur qui casse le filtre IDCC de justicelibre).
"""
import pathlib

from france_opendata.kali import parse_kali_article, parse_kali_conteneur, strip_html

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_parse_conteneur():
    row = parse_kali_conteneur((FIXTURES / "kali_conteneur.xml").read_bytes())
    assert row["id"] == "KALICONT000005635507"
    assert row["idcc"] == "1978"
    assert row["titre"].startswith("Convention collective nationale des fleuristes")
    assert row["etat"] == "VIGUEUR_ETEN"
    assert row["date_publi"] == "2021-12-24"


def test_parse_article():
    row = parse_kali_article((FIXTURES / "kali_article.xml").read_bytes())
    assert row["id"] == "KALIARTI000054337658"
    # LE rattachement (conteneur = frère de TEXTE dans CONTEXTE)
    assert row["conteneur_id"] == "KALICONT000018773893"
    assert row["texte_id"] == "KALITEXT000054337649"
    assert row["texte_titre"]  # intitulé de l'avenant/accord parent
    assert row["texte_nature"] == "Accord"
    assert row["date_signature"] == "2026-02-13"
    assert row["num"] == "5"
    assert row["etat"] == "VIGUEUR_ETEN"
    assert row["date_debut"] == "2026-05-01"
    assert row["date_fin"] is None  # 2999-01-01 DILA → None
    assert row["texte"]  # BLOC_TEXTUEL aplati


def test_strip_html_blocks_and_entities():
    assert strip_html("<p>Ligne 1</p><p>Ligne&nbsp;2 &amp; fin</p>") == "Ligne 1\n\nLigne 2 & fin"


def test_parse_garbage_returns_none():
    assert parse_kali_conteneur(b"not xml") is None
    assert parse_kali_article(b"<broken>") is None
