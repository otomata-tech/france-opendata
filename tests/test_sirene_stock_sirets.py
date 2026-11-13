"""Stock ÉTABLISSEMENT — un LOT de SIRET en un scan, avec l'effectif et le caractère
employeur de chaque établissement.

`headquarters_addresses` ne rendait que le siège d'un SIREN, et sa forme jetait la
tranche d'effectif que le SELECT venait de lire ; `lookup_siret` ne prenait qu'un SIRET.
Les tests montent un parquet synthétique aux noms de colonnes INSEE — pas le vrai stock.
"""
from __future__ import annotations

import duckdb
import pytest

from france_opendata import sirene_stock as ss
from france_opendata.erreurs import ParametreInvalide

_SIEGE = "44397593300012"
_SECONDAIRE = "44397593300020"
_NON_EMPLOYEUR = "85239977800015"


def _ligne(siret: str, siege: bool, tranche: str, annee: str, employeur: str,
           debut: str, voie: str) -> dict:
    ligne = {src: None for src in ss._COLUMN_MAP}
    ligne.update({
        "siren": siret[:9], "siret": siret, "nic": siret[9:], "etablissementSiege": siege,
        "etatAdministratifEtablissement": "A", "dateDebut": debut,
        "activitePrincipaleEtablissement": "43.21A",
        "trancheEffectifsEtablissement": tranche, "anneeEffectifsEtablissement": annee,
        "caractereEmployeurEtablissement": employeur, "numeroVoieEtablissement": "3",
        "typeVoieEtablissement": "RUE", "libelleVoieEtablissement": voie,
        "codePostalEtablissement": "35000", "libelleCommuneEtablissement": "RENNES",
    })
    return ligne


@pytest.fixture()
def stock(tmp_path, monkeypatch):
    lignes = [
        _ligne(_SIEGE, True, "12", "2023", "O", "2020-01-01", "DU SIEGE"),
        _ligne(_SECONDAIRE, False, "03", "2023", "O", "2021-01-01", "DE L'ATELIER"),
        # Même SIRET, période PLUS ANCIENNE : ne doit jamais gagner.
        _ligne(_SECONDAIRE, False, "02", "2019", "O", "2015-01-01", "ANCIENNE"),
        _ligne(_NON_EMPLOYEUR, True, None, None, "N", "2019-06-01", "DU FOUR"),
    ]
    cols = list(ss._COLUMN_MAP)
    path = tmp_path / "Stock.parquet"
    con = duckdb.connect()
    types = {"etablissementSiege": "BOOLEAN", "dateDebut": "DATE"}
    con.execute("CREATE TABLE e (" + ", ".join(
        f'"{c}" {types.get(c, "VARCHAR")}' for c in cols) + ")")
    con.executemany(f"INSERT INTO e VALUES ({', '.join('?' * len(cols))})",
                    [[ligne[c] for c in cols] for ligne in lignes])
    con.execute(f"COPY e TO '{path}' (FORMAT PARQUET)")
    monkeypatch.setattr(ss, "parquet_path", lambda: str(path))
    return path


def test_un_lot_rend_effectif_annee_et_caractere_employeur(stock):
    out = ss.lookup_sirets([_SIEGE, _SECONDAIRE, _NON_EMPLOYEUR])
    assert out["count"] == 3 and out["erreurs"] == []
    sec = out["items"][_SECONDAIRE]
    assert (sec["tranche_effectifs"], sec["annee_effectifs"], sec["caractere_employeur"]) \
        == ("03", "2023", "O")
    assert sec["is_headquarters"] is False and sec["street"] == "3 RUE DE L'ATELIER"
    ne = out["items"][_NON_EMPLOYEUR]
    assert ne["caractere_employeur"] == "N" and ne["tranche_effectifs"] is None


def test_erreurs_par_element_sans_casser_le_lot(stock):
    out = ss.lookup_sirets([_SIEGE, "443 975 933 00012", "123", "99999999999999", ""])
    assert set(out["items"]) == {_SIEGE}
    assert out["erreurs"] == [{"siret": "123", "code": "siret_invalide"},
                              {"siret": "", "code": "siret_invalide"},
                              {"siret": "99999999999999", "code": "introuvable"}]


def test_le_lot_au_dela_de_la_borne_est_refuse(stock, monkeypatch):
    monkeypatch.setattr(ss, "SIRETS_MAX", 2)
    with pytest.raises(ParametreInvalide, match="sirets"):
        ss.lookup_sirets([_SIEGE, _SECONDAIRE, _NON_EMPLOYEUR])
    # Les doublons ne comptent qu'une fois.
    assert ss.lookup_sirets([_SIEGE, _SIEGE, _SECONDAIRE])["count"] == 2


def test_le_siege_d_un_siren_porte_aussi_l_effectif(stock):
    hq = ss.headquarters_addresses(["443975933"])["443975933"]
    assert (hq["tranche_effectifs"], hq["caractere_employeur"]) == ("12", "O")


def test_lot_vide(stock):
    assert ss.lookup_sirets([]) == {"items": {}, "erreurs": [], "count": 0}
