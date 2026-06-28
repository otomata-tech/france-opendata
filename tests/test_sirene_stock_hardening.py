"""Durcissement intérimaire du reader SIRENE stock (ADR 0028).

Vérifie les deux gardes posées à la source partagée tant que FOD n'est pas
extrait : timeout DUR par requête (watchdog `conn.interrupt`) + plafond de
concurrence (scans en vol + total en attente). Ces tests n'utilisent pas le
parquet : des requêtes DuckDB synthétiques (`SELECT`, `range()`) suffisent à
exercer le helper `_query`.
"""
from __future__ import annotations

import pytest

from france_opendata import sirene_stock as ss


def test_query_ok_passes_through():
    assert ss._query("SELECT 42", fetch="one")[0] == 42
    assert ss._query("SELECT * FROM range(3)", fetch="all") == [(0,), (1,), (2,)]


def test_query_timeout_interrupts_heavy_scan(monkeypatch):
    # Watchdog très court → la requête lourde est interrompue, pas attendue.
    monkeypatch.setattr(ss, "_QUERY_TIMEOUT_S", 0.3)
    heavy = "SELECT count(*) FROM range(100000000000) t1, range(100000) t2"
    with pytest.raises(ss.StockQueryTimeout):
        ss._query(heavy, fetch="one")
    # La garde libère bien les sémaphores : un appel normal repasse derrière.
    assert ss._query("SELECT 1", fetch="one")[0] == 1


def test_inflight_cap_rejects_immediately():
    # Sature le plafond global (rejet non bloquant), puis vérifie le rejet.
    held = []
    while ss._INFLIGHT_SEM.acquire(blocking=False):
        held.append(1)
    try:
        with pytest.raises(ss.StockOverloaded):
            ss._query("SELECT 1", fetch="one")
    finally:
        for _ in held:
            ss._INFLIGHT_SEM.release()
    # Plafond rendu : tout repart.
    assert ss._query("SELECT 1", fetch="one")[0] == 1


def test_scan_cap_times_out_when_all_slots_busy(monkeypatch):
    monkeypatch.setattr(ss, "_ACQUIRE_TIMEOUT_S", 0.2)
    held = []
    while ss._SCAN_SEM.acquire(blocking=False):
        held.append(1)
    try:
        with pytest.raises(ss.StockOverloaded):
            ss._query("SELECT 1", fetch="one")
    finally:
        for _ in held:
            ss._SCAN_SEM.release()
    assert ss._query("SELECT 1", fetch="one")[0] == 1


def test_bad_fetch_mode_rejected():
    with pytest.raises(ValueError):
        ss._query("SELECT 1", fetch="nope")


# --- Mode partitionné (ADR 0028, barreau 3) --------------------------------

def test_partition_detection(monkeypatch):
    # Dossier local → partitionné ; fichier .parquet ou distant → mono-fichier.
    monkeypatch.setattr(ss, "parquet_path", lambda: "/data/sirene/partitioned")
    assert ss._is_partitioned() is True
    assert "hive_partitioning=true" in ss._from_parquet()
    assert "/data/sirene/partitioned/**/*.parquet" in ss._from_parquet()

    monkeypatch.setattr(ss, "parquet_path", lambda: "/data/x/Stock.parquet")
    assert ss._is_partitioned() is False
    assert ss._from_parquet() == "read_parquet('/data/x/Stock.parquet')"

    monkeypatch.setattr(ss, "parquet_path", lambda: "s3://b/Stock.parquet")
    assert ss._is_partitioned() is False


def test_search_dept_pruning_predicate(monkeypatch):
    # En mode partitionné, search(departement=) doit injecter le prédicat de
    # partition `dept = ?` (pruning) EN PLUS du filtre fin sur le code postal.
    monkeypatch.setattr(ss, "parquet_path", lambda: "/data/sirene/partitioned")
    captured = {}

    def fake_query(sql, params=None, *, fetch):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(ss, "_query", fake_query)
    ss.search(departement="13", limit=10)
    assert "dept = ?" in captured["sql"]
    assert "13" in captured["params"]  # pruning sur la partition
    # DOM : départément 3 chars → partition sur les 2 premiers, filtre fin sur 3.
    ss.search(departement="971", limit=10)
    assert "971" in captured["params"] and "97" in captured["params"]


def test_search_no_dept_predicate_when_single_file(monkeypatch):
    monkeypatch.setattr(ss, "parquet_path", lambda: "/data/x/Stock.parquet")
    captured = {}
    monkeypatch.setattr(ss, "_query", lambda sql, params=None, *, fetch: captured.update(sql=sql) or [])
    ss.search(departement="13", limit=10)
    assert "dept = ?" not in captured["sql"]  # pas de colonne dept en mono-fichier


def test_search_code_postal_prunes_partition(monkeypatch):
    # Une recherche par CODE POSTAL seul (sans departement) doit pruner la
    # partition `dept` dérivée du préfixe 2 chars — plus de full-scan des 43M lignes.
    monkeypatch.setattr(ss, "parquet_path", lambda: "/data/sirene/partitioned")
    captured = {}
    monkeypatch.setattr(ss, "_query",
                        lambda sql, params=None, *, fetch: captured.update(sql=sql, params=params) or [])
    ss.search(code_postal="13001", limit=10)
    assert "dept = ?" in captured["sql"]
    assert "13" in captured["params"]


def test_search_code_commune_does_not_prune(monkeypatch):
    # Pas de pruning dérivé de la commune INSEE (communes frontalières à code
    # postal d'un dept voisin → risque d'écarter des établissements).
    monkeypatch.setattr(ss, "parquet_path", lambda: "/data/sirene/partitioned")
    captured = {}
    monkeypatch.setattr(ss, "_query",
                        lambda sql, params=None, *, fetch: captured.update(sql=sql, params=params) or [])
    ss.search(code_commune="13201", limit=10)
    assert "dept = ?" not in captured["sql"]


def test_dept_partition_helper():
    assert ss._dept_partition("13", None) == "13"       # departement explicite
    assert ss._dept_partition("971", None) == "97"      # DOM 3 chars → partition 2 chars
    assert ss._dept_partition(None, "13001") == "13"    # dérivé du code postal
    assert ss._dept_partition(None, "97400") == "97"    # DOM
    assert ss._dept_partition(None, None) is None
    assert ss._dept_partition(None, "  ") is None        # code postal vide → pas de pruning
    assert ss._dept_partition("13", "75002") == "13"    # departement prioritaire
