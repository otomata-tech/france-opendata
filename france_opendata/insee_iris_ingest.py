"""Ingestion one-shot : bases INSEE IRIS (RP 2021) -> parquet compact bundlé.

Source (open data, géographie 2024) :
- Population : base-ic-evol-struct-pop-2021 (page INSEE 8268806)
- Logement  : base-ic-logement-2021        (page INSEE 8268838)

Produit un parquet ~49k lignes (1 par IRIS + communes non découpées TYP_IRIS='Z'),
keyé par code IRIS (9) et code commune COM (5, inclut les arrondissements municipaux
ARM : 132xx Marseille, 751xx Paris, 6938x Lyon). Effectifs arrondis à l'entier.
"""
from __future__ import annotations

import sys
import duckdb

POP_CSV = sys.argv[1]
LOG_CSV = sys.argv[2]
OUT = sys.argv[3]

# Codes en VARCHAR (préserver les zéros de tête). Effectifs = DOUBLE (INSEE diffuse
# des estimations décimales) -> ROUND en entier à la sortie.
con = duckdb.connect()
con.execute(f"CREATE VIEW pop AS SELECT * FROM read_csv('{POP_CSV}', delim=';', header=true, all_varchar=true)")
con.execute(f"CREATE VIEW log AS SELECT * FROM read_csv('{LOG_CSV}', delim=';', header=true, all_varchar=true)")

# round(NULL) -> NULL ; TRY_CAST protège des champs vides.
def r(col: str) -> str:
    return f"CAST(ROUND(TRY_CAST({col} AS DOUBLE)) AS INTEGER)"

con.execute(
    f"""
    COPY (
      SELECT
        p.IRIS                          AS iris,
        p.COM                           AS com,
        p.TYP_IRIS                      AS typ_iris,
        p.LAB_IRIS                      AS lab_iris,
        {r('p.P21_POP')}                AS pop,
        {r('p.P21_POP0019')}            AS pop_0_19,
        {r('p.P21_POP2064')}            AS pop_20_64,
        {r('p.P21_POP65P')}             AS pop_65p,
        {r('l.P21_LOG')}                AS logements,
        {r('l.P21_RP')}                 AS res_principales,
        {r('l.P21_RSECOCC')}            AS res_secondaires,
        {r('l.P21_LOGVAC')}             AS logements_vacants,
        {r('l.P21_MAISON')}             AS maisons,
        {r('l.P21_APPART')}             AS appartements,
        {r('l.P21_RPAPPART')}           AS rp_en_appartement
      FROM pop p
      LEFT JOIN log l USING (IRIS)
      ORDER BY p.IRIS
    ) TO '{OUT}' (FORMAT parquet, COMPRESSION zstd);
    """
)

n, ncom = con.execute(
    f"SELECT count(*), count(DISTINCT com) FROM read_parquet('{OUT}')"
).fetchone()
print(f"parquet écrit : {OUT}  ({n} IRIS, {ncom} communes/arrondissements)")
# Sanity : Marseille 1er arrondissement (COM=13201)
rows = con.execute(
    f"SELECT iris, pop, res_principales, rp_en_appartement FROM read_parquet('{OUT}') "
    "WHERE com='13201' ORDER BY iris LIMIT 3"
).fetchall()
print("échantillon 13201 :", rows)
