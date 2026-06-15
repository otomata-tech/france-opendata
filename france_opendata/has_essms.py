"""HAS — résultats d'évaluation des ESSMS (open data, DuckDB sur parquet).

Source : data.gouv.fr / HAS (minio.data.has-sante.fr) — évaluations des
établissements et services sociaux et médico-sociaux (référentiel EDS v2022+).
Trois parquets : `par_essms` (1 ligne / établissement × évaluation, pivot),
`par_eval` (évaluations agrégées), `echelle_qualite` (scores Qualiscope).

Nécessite l'extra **`france-opendata[sante]`** (DuckDB). Les parquets sont lus
à distance via httpfs en **range requests** (predicate/projection pushdown) —
pas de téléchargement complet. Client de données : filtres + dimensions ; les
agrégats métier (radar 9 thématiques, histogrammes Qualiscope) restent à
l'appelant (ex. benchmark applicatif).
"""
from __future__ import annotations

from typing import Any, Optional

BASE = "https://minio.data.has-sante.fr/synae/data/prod/open_data"
PAR_ESSMS_URL = f"{BASE}/open_data_par_essms.parquet"
PAR_EVAL_URL = f"{BASE}/open_data_par_eval.parquet"
ECHELLE_URL = f"{BASE}/open_data_echelle_qualite.parquet"

PUBLICS = ("PA", "PHA", "PHE", "AHI", "PDS", "PE/PJJ")


class HasEssmsClient:
    def __init__(self):
        self._con = None

    def _connect(self):
        if self._con is None:
            import duckdb  # extra [sante] ; lazy pour ne pas casser sans l'extra
            con = duckdb.connect()
            con.execute("INSTALL httpfs; LOAD httpfs;")
            con.execute(f"CREATE VIEW par_essms AS SELECT * FROM read_parquet('{PAR_ESSMS_URL}')")
            con.execute(f"CREATE VIEW par_eval AS SELECT * FROM read_parquet('{PAR_EVAL_URL}')")
            con.execute(f"CREATE VIEW echelle AS SELECT * FROM read_parquet('{ECHELLE_URL}')")
            self._con = con
        return self._con

    def _where(
        self,
        *,
        region_code: Optional[str] = None,
        region_libelle: Optional[str] = None,
        departement_code: Optional[str] = None,
        categ_finess_code=None,
        secteur: Optional[str] = None,
        type_structure: Optional[str] = None,
        statut_juridique: Optional[str] = None,
        publics: Optional[list[str]] = None,
        annee_min: Optional[int] = None,
        annee_max: Optional[int] = None,
    ) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []

        def add_in(col, val):
            if val is None:
                return
            vals = val if isinstance(val, list) else [val]
            clauses.append(f'"{col}" IN ({",".join(["?"] * len(vals))})')
            params.extend(vals)

        add_in("region_code", region_code)
        add_in("region_libelle", region_libelle)
        add_in("departement_code", departement_code)
        add_in("essms_categ_finess_code", categ_finess_code)
        for col, val in (("essms_secteur", secteur), ("essms_type_structure", type_structure),
                         ("essms_statut_juridique", statut_juridique)):
            if val:
                clauses.append(f'"{col}" = ?')
                params.append(val)
        for p in (publics or []):
            if p not in PUBLICS:
                raise ValueError(f"public inconnu: {p} (attendus: {list(PUBLICS)})")
            clauses.append(f'"essms_{p}" = 1')
        if annee_min is not None:
            clauses.append("YEAR(eval_date_debut) >= ?")
            params.append(annee_min)
        if annee_max is not None:
            clauses.append("YEAR(eval_date_debut) <= ?")
            params.append(annee_max)
        return ("WHERE " + " AND ".join(clauses) if clauses else "", params)

    def _rows(self, sql: str, params: list) -> list[dict[str, Any]]:
        cur = self._connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def count(self, **filters) -> int:
        where, params = self._where(**filters)
        return int(self._connect().execute(f"SELECT COUNT(*) FROM par_essms {where}", params).fetchone()[0])

    def search(self, *, columns: Optional[list[str]] = None, limit: int = 50, **filters) -> dict[str, Any]:
        """Établissements évalués filtrés. `columns` None = un sous-ensemble clé.

        Filtres : region_code/region_libelle, departement_code, categ_finess_code,
        secteur ('Social'|'Médico-social'), type_structure ('Etablissement'|'Service'),
        statut_juridique, publics (liste ⊆ PUBLICS), annee_min/annee_max.
        """
        sel = ", ".join(f'"{c}"' for c in columns) if columns else (
            'finess_geo, raison_sociale, region_libelle, departement_code, '
            'essms_categ_finess_libelle, essms_secteur, essms_type_structure, '
            'essms_statut_juridique, eval_date_debut, indice_qualite'
        )
        where, params = self._where(**filters)
        total = self.count(**filters)
        rows = self._rows(f"SELECT {sel} FROM par_essms {where} LIMIT {int(limit)}", params)
        return {"total": total, "returned": len(rows), "results": rows}

    def dimensions(self) -> dict[str, list]:
        """Valeurs distinctes des axes de filtrage (régions, secteurs, catégories, années)."""
        con = self._connect()
        out: dict[str, list] = {}
        for col in ("region_libelle", "essms_secteur", "essms_type_structure",
                    "essms_statut_juridique", "essms_categ_finess_libelle"):
            out[col] = con.execute(
                f'SELECT "{col}" AS v, COUNT(*) AS n FROM par_essms GROUP BY 1 ORDER BY n DESC'
            ).fetchall()
        out["annees"] = con.execute(
            "SELECT YEAR(eval_date_debut) AS y, COUNT(*) AS n FROM par_essms GROUP BY 1 ORDER BY 1"
        ).fetchall()
        return out
