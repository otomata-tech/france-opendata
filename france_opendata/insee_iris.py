"""INSEE IRIS — recensement à la maille infracommunale (open data, DuckDB local).

L'IRIS (~2 000 habitants) est la maille « quartier » de l'INSEE : les communes de
≥10 000 hab (et la plupart de 5 000-10 000) sont découpées en IRIS. Les communes non
découpées apparaissent comme un IRIS unique (`typ_iris='Z'`), pour une couverture
nationale complète.

Contrairement à Mélodi (maille COMMUNE seulement, cf. `InseeMelodiClient`), l'IRIS
n'est PAS diffusé par API : la source est les fichiers bulk « bases infracommunales »
(RP 2021, géographie 2024). On les ingère une fois en un parquet compact **bundlé
dans le package** (~1 Mo, 49k lignes) — voir `insee_iris_ingest.py`. Lecture DuckDB
in-process : lookup par code instantané, aucune dépendance réseau au runtime.

Le code commune (`com`) inclut les **arrondissements municipaux** : Marseille
132xx, Paris 751xx, Lyon 6938x — d'où le lookup `by_commune` qui retourne tous les
IRIS d'un arrondissement.

Millésime pointé par le fichier bundlé ; surchargeable par `INSEE_IRIS_PARQUET_PATH`.
Nécessite l'extra **`france-opendata[stock]`** (DuckDB).

Champs retournés (effectifs RP 2021 arrondis) :
- `iris` (9), `com` (5), `typ_iris` (H habitat / A activité / D divers / Z commune
  non découpée), `lab_iris` (libellé INSEE, souvent numérique) ;
- `pop`, `pop_0_19`, `pop_20_64`, `pop_65p` ;
- `logements`, `res_principales`, `res_secondaires`, `logements_vacants`,
  `maisons`, `appartements`, `rp_en_appartement`.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Optional

_COLUMNS = (
    "iris", "com", "typ_iris", "lab_iris",
    "pop", "pop_0_19", "pop_20_64", "pop_65p",
    "logements", "res_principales", "res_secondaires", "logements_vacants",
    "maisons", "appartements", "rp_en_appartement",
)


def parquet_path() -> str:
    """Chemin du parquet IRIS : env `INSEE_IRIS_PARQUET_PATH` ou fichier bundlé."""
    env = os.environ.get("INSEE_IRIS_PARQUET_PATH")
    if env:
        return env
    from importlib import resources
    return str(resources.files("france_opendata").joinpath("data/iris_2021.parquet"))


class InseeIrisClient:
    """Recensement INSEE à l'IRIS (parquet local bundlé). Sans clé."""

    def __init__(self):
        self._con = None
        self._lock = threading.Lock()

    def _connect(self):
        # Connexion DuckDB read-only cachée + view sur le parquet bundlé. duckdb en
        # import lazy (extra [stock]) pour ne pas casser l'import sans l'extra.
        if self._con is None:
            import duckdb
            con = duckdb.connect(database=":memory:")
            con.execute(
                f"CREATE VIEW iris AS SELECT * FROM read_parquet('{parquet_path()}')"
            )
            self._con = con
        return self._con

    def _rows(self, where: str, params: list, limit: int) -> list[dict[str, Any]]:
        cols = ", ".join(_COLUMNS)
        with self._lock:
            con = self._connect()
            cur = con.execute(
                f"SELECT {cols} FROM iris WHERE {where} ORDER BY iris LIMIT {int(limit)}",
                params,
            )
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    def by_iris(self, code_iris: str) -> Optional[dict[str, Any]]:
        """Fiche d'un IRIS par son code à 9 chiffres. None si inconnu."""
        rows = self._rows("iris = ?", [str(code_iris)], limit=1)
        return rows[0] if rows else None

    def by_commune(self, code_com: str, limit: int = 200) -> dict[str, Any]:
        """Tous les IRIS d'une commune / arrondissement municipal par code INSEE (5).

        Retourne aussi les totaux commune (somme des IRIS) pour un usage direct.
        `nb_iris`=0 → code commune inconnu. Une commune non découpée a 1 IRIS `Z`.
        """
        rows = self._rows("com = ?", [str(code_com)], limit=limit)
        totals: dict[str, Any] = {}
        if rows:
            for k in ("pop", "logements", "res_principales", "res_secondaires",
                      "logements_vacants", "maisons", "appartements", "rp_en_appartement"):
                vals = [r[k] for r in rows if r.get(k) is not None]
                totals[k] = sum(vals) if vals else None
        return {"com": str(code_com), "nb_iris": len(rows), "totaux": totals, "iris": rows}
