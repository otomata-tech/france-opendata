"""Stock UNITÉ LÉGALE — le lookup qui porte `categorieEntreprise` (PME/ETI/GE).

Ce stock existe parce que l'établissement ne dit RIEN de l'appartenance à un
groupe : une filiale peut être petite à l'effectif de son établissement et rester
une GE au sens INSEE (calcul sur le périmètre groupe). Les tests montent un
parquet synthétique aux noms de colonnes INSEE — pas le vrai stock (2 Go) — pour
exercer le SQL, le mapping snake_case et la déduplication.
"""
from __future__ import annotations

import duckdb
import pytest

from france_opendata import sirene_stock as ss


@pytest.fixture()
def ul_parquet(tmp_path, monkeypatch):
    """Parquet unité légale minimal : une PME, une filiale GE, et un SIREN en
    double période (pour exercer le QUALIFY)."""
    path = tmp_path / "unites_legales.parquet"
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE ul AS SELECT * FROM (VALUES
          ('443975933', 'O', 'A', DATE '2002-10-30', DATE '2020-01-01',
           'GTIE RENNES', '', '', '', 'GE', 2023, '12', 2023, 5710,
           '43.21A', 'NAFRev2', '00039', 'N', 'N', 'O', ''),
          ('852399778', 'O', 'A', DATE '2019-06-01', DATE '2019-06-01',
           'BOULANGERIE DU COIN', '', '', '', 'PME', 2023, '11', 2023, 5710,
           '10.71C', 'NAFRev2', '00012', 'N', 'N', 'O', ''),
          -- Même SIREN, période PLUS ANCIENNE : ne doit jamais gagner.
          ('852399778', 'O', 'A', DATE '2019-06-01', DATE '2019-06-01',
           'ANCIENNE RAISON', '', '', '', 'ETI', 2019, '21', 2019, 5710,
           '10.71C', 'NAFRev2', '00012', 'N', 'N', 'O', '')
        ) AS t(siren, statutDiffusionUniteLegale, etatAdministratifUniteLegale,
               dateCreationUniteLegale, dateDebut, denominationUniteLegale,
               nomUniteLegale, nomUsageUniteLegale, sigleUniteLegale,
               categorieEntreprise, anneeCategorieEntreprise,
               trancheEffectifsUniteLegale, anneeEffectifsUniteLegale,
               categorieJuridiqueUniteLegale, activitePrincipaleUniteLegale,
               nomenclatureActivitePrincipaleUniteLegale, nicSiegeUniteLegale,
               economieSocialeSolidaireUniteLegale, societeMissionUniteLegale,
               caractereEmployeurUniteLegale, identifiantAssociationUniteLegale)
        """
    )
    # La période récente du SIREN dupliqué, écrite après coup pour que le tri par
    # dateDebut ait quelque chose à départager.
    con.execute(
        "UPDATE ul SET dateDebut = DATE '2023-01-01' "
        "WHERE siren = '852399778' AND denominationUniteLegale = 'BOULANGERIE DU COIN'"
    )
    con.execute(f"COPY ul TO '{path}' (FORMAT PARQUET)")
    monkeypatch.setenv("SIRENE_UL_PARQUET_PATH", str(path))
    return path


def test_available_reflects_mount(tmp_path, monkeypatch):
    monkeypatch.setenv("SIRENE_UL_PARQUET_PATH", str(tmp_path / "absent.parquet"))
    assert ss.ul_stock_available() is False
    monkeypatch.setenv("SIRENE_UL_PARQUET_PATH", "s3://bucket/unites_legales.parquet")
    assert ss.ul_stock_available() is True


def test_lookup_returns_categorie_in_snake_case(ul_parquet):
    out = ss.lookup_unites_legales(["443975933"])
    assert out["443975933"]["categorie_entreprise"] == "GE"
    assert out["443975933"]["denomination"] == "GTIE RENNES"
    assert out["443975933"]["tranche_effectifs"] == "12"
    # Les champs de personne physique ne sont pas transportés.
    assert "prenom1" not in out["443975933"]


def test_lookup_batches_and_skips_unknown(ul_parquet):
    out = ss.lookup_unites_legales(["443975933", "852399778", "000000000"])
    assert set(out) == {"443975933", "852399778"}
    assert out["852399778"]["categorie_entreprise"] == "PME"


def test_lookup_keeps_latest_period_only(ul_parquet):
    """Un SIREN à deux périodes rend UNE ligne — la plus récente. Sans ça,
    l'appelant qui joint dupliquerait ses lignes en silence."""
    out = ss.lookup_unites_legales(["852399778"])
    assert out["852399778"]["denomination"] == "BOULANGERIE DU COIN"
    assert out["852399778"]["categorie_entreprise"] == "PME"


def test_lookup_empty_input_does_not_query(ul_parquet):
    assert ss.lookup_unites_legales([]) == {}
    assert ss.lookup_unites_legales(["", "  "]) == {}
