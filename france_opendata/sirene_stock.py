"""DuckDB read-only over StockEtablissement.parquet (~35M rows, INSEE SIRENE).

Reader local de la stock SIRENE (le parquet INSEE complet). Source unique partagée :
oto-mcp (tools/API) ET les apps co-localisées (ex. tuls) consomment CE module
plutôt que de dupliquer la requête ou de maintenir une table PG en doublon.

Le parquet est résolu via `SIRENE_STOCK_PARQUET_PATH` (défaut
`/opt/oto-mcp/data/sirene/StockEtablissement.parquet`). Trois sources possibles :
- chemin local (défaut) ;
- `s3://bucket/key` — lu via httpfs, creds DuckDB depuis l'env `SIRENE_STOCK_S3_*`
  (ENDPOINT, KEY_ID, SECRET, + REGION/URL_STYLE optionnels) ;
- `https://…` public — lu via httpfs sans credential.
Le distant fait des range reads (pruning de row groups) : seuls les chunks utiles
transitent, pas les ~2 Go. Lookup par siren ~0.6 s, scan filtré quelques secondes.

DuckDB en lecture seule : connexion à la demande, view sur le parquet, query. Pas
d'index — DuckDB lit les row groups + columnar pruning. Lookups par `siren`/`siret`
sur 35M = 50-200ms à froid, ~10-50ms à chaud (page cache OS). Pour enrichir une LISTE
de SIREN, utiliser `lookup_sieges`/`headquarters_addresses` (UN seul scan, pas N).

Retours = dicts snake_case (depuis les camelCase INSEE), NULLs explicites.

Nécessite l'extra : `france-opendata[stock]` (duckdb).
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Iterable, Optional

import duckdb


DEFAULT_PATH = "/opt/oto-mcp/data/sirene/StockEtablissement.parquet"


def parquet_path() -> str:
    return os.environ.get("SIRENE_STOCK_PARQUET_PATH", DEFAULT_PATH)


# Colonnes parquet INSEE → snake_case stable côté API.
_COLUMN_MAP = {
    "siren": "siren",
    "siret": "siret",
    "nic": "nic",
    "etablissementSiege": "is_siege",
    "etatAdministratifEtablissement": "etat",
    "dateCreationEtablissement": "date_creation",
    "dateDebut": "date_debut",
    "denominationUsuelleEtablissement": "denomination",
    "enseigne1Etablissement": "enseigne_1",
    "enseigne2Etablissement": "enseigne_2",
    "enseigne3Etablissement": "enseigne_3",
    "activitePrincipaleEtablissement": "naf",
    "nomenclatureActivitePrincipaleEtablissement": "naf_nomenclature",
    "trancheEffectifsEtablissement": "tranche_effectifs",
    "anneeEffectifsEtablissement": "annee_effectifs",
    "complementAdresseEtablissement": "complement_adresse",
    "numeroVoieEtablissement": "numero_voie",
    "indiceRepetitionEtablissement": "indice_repetition",
    "typeVoieEtablissement": "type_voie",
    "libelleVoieEtablissement": "libelle_voie",
    "codePostalEtablissement": "code_postal",
    "libelleCommuneEtablissement": "libelle_commune",
    "codeCommuneEtablissement": "code_commune",
    "codeCedexEtablissement": "code_cedex",
    "libelleCedexEtablissement": "libelle_cedex",
    "distributionSpecialeEtablissement": "distribution_speciale",
    "coordonneeLambertAbscisseEtablissement": "lambert_x",
    "coordonneeLambertOrdonneeEtablissement": "lambert_y",
    "libelleCommuneEtrangerEtablissement": "libelle_commune_etranger",
    "codePaysEtrangerEtablissement": "code_pays_etranger",
    "libellePaysEtrangerEtablissement": "libelle_pays_etranger",
    "dateDernierTraitementEtablissement": "date_dernier_traitement",
}

_SELECT_CLAUSE = ", ".join(f'"{src}" AS {dst}' for src, dst in _COLUMN_MAP.items())


def _is_remote(path: str) -> bool:
    return path.startswith(("s3://", "http://", "https://"))


def _configure_remote(conn: duckdb.DuckDBPyConnection, path: str) -> None:
    """Active httpfs pour lire un parquet distant (S3 ou HTTPS public). Pour s3://,
    pose un SECRET DuckDB depuis l'env (SIRENE_STOCK_S3_*). Range reads + pruning
    de row groups → seuls les chunks utiles transitent (pas le fichier entier)."""
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("SET enable_http_metadata_cache=true;")
    conn.execute("SET enable_object_cache=true;")
    if not path.startswith("s3://"):
        return  # HTTPS public : aucun credential
    endpoint = os.environ.get("SIRENE_STOCK_S3_ENDPOINT")
    key = os.environ.get("SIRENE_STOCK_S3_KEY_ID")
    secret = os.environ.get("SIRENE_STOCK_S3_SECRET")
    if not (endpoint and key and secret):
        raise RuntimeError(
            "Parquet S3 distant : définir SIRENE_STOCK_S3_ENDPOINT, "
            "SIRENE_STOCK_S3_KEY_ID et SIRENE_STOCK_S3_SECRET."
        )
    region = os.environ.get("SIRENE_STOCK_S3_REGION", "fr-par")
    url_style = os.environ.get("SIRENE_STOCK_S3_URL_STYLE", "vhost")
    # endpoint sans scheme pour DuckDB ; échappe les quotes dans les littéraux.
    ep = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    def _q(v: str) -> str:
        return v.replace("'", "''")
    conn.execute(
        "CREATE OR REPLACE SECRET sirene_s3 (TYPE S3, "
        f"KEY_ID '{_q(key)}', SECRET '{_q(secret)}', ENDPOINT '{_q(ep)}', "
        f"REGION '{_q(region)}', URL_STYLE '{_q(url_style)}', USE_SSL true)"
    )


def _connect() -> duckdb.DuckDBPyConnection:
    """Une nouvelle connexion read-only par appel. DuckDB est rapide à ouvrir
    (~ms) et les connections ne sont pas thread-safe pour des queries concurrentes,
    donc on évite de partager. La page cache OS fait le travail de mise en cache.

    Si le parquet est distant (s3:// ou https://), active httpfs sur la connexion."""
    conn = duckdb.connect(database=":memory:", read_only=False)
    path = parquet_path()
    if _is_remote(path):
        _configure_remote(conn, path)
    return conn


def _from_parquet() -> str:
    """Clause FROM pointant le parquet — quoté/échappé via DuckDB."""
    return f"read_parquet('{parquet_path()}')"


def _row_to_dict(row: tuple, columns: list[str]) -> dict[str, Any]:
    """Normalise les types non-JSON-serializable issus du parquet : dates
    DuckDB → strings ISO, strings vides → None, etablissementSiege → bool."""
    out: dict[str, Any] = {}
    for col, val in zip(columns, row):
        if val == "" or val is None:
            out[col] = None
        elif isinstance(val, (_dt.datetime, _dt.date)):
            out[col] = val.isoformat()
        elif col == "is_siege":
            out[col] = bool(val)
        else:
            out[col] = val
    return out


def _output_columns() -> list[str]:
    return list(_COLUMN_MAP.values())


def lookup_siege(siren: str) -> Optional[dict[str, Any]]:
    """Renvoie le siège (etablissementSiege=True) pour un SIREN, ou None.

    Si plusieurs sièges existent dans l'historique (changement d'adresse rare),
    on prend celui dont la période est encore ouverte (date_debut max).
    """
    sql = (
        f"SELECT {_SELECT_CLAUSE} FROM {_from_parquet()} "
        "WHERE siren = ? AND etablissementSiege = TRUE "
        "ORDER BY dateDebut DESC NULLS LAST "
        "LIMIT 1"
    )
    with _connect() as conn:
        row = conn.execute(sql, [siren]).fetchone()
    return _row_to_dict(row, _output_columns()) if row else None


def lookup_sieges(sirens: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Sièges pour une LISTE de SIREN en UNE requête (1 scan parquet, pas N).

    Réplique `lookup_siege` (siège à période ouverte) en batch via QUALIFY
    ROW_NUMBER partitionné par siren. Renvoie {siren: dict siège} ; les SIRENs
    introuvables sont absents du dict. À privilégier pour enrichir une liste.
    """
    uniq = [s for s in dict.fromkeys(sirens) if s]
    if not uniq:
        return {}
    placeholders = ", ".join("?" * len(uniq))
    sql = (
        f"SELECT {_SELECT_CLAUSE} FROM {_from_parquet()} "
        f"WHERE siren IN ({placeholders}) AND etablissementSiege = TRUE "
        "QUALIFY ROW_NUMBER() OVER "
        "(PARTITION BY siren ORDER BY dateDebut DESC NULLS LAST) = 1"
    )
    with _connect() as conn:
        rows = conn.execute(sql, uniq).fetchall()
    cols = _output_columns()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = _row_to_dict(r, cols)
        out[d["siren"]] = d
    return out


def _normalize_address(etab: dict[str, Any]) -> dict[str, Any]:
    """Établissement snake_case → forme stable adresse {street, postal_code, city,
    status…} pour les consommateurs historiques (tuls company-lookup, etc.)."""
    num = (etab.get("numero_voie") or "").strip()
    type_voie = (etab.get("type_voie") or "").strip()
    voie = (etab.get("libelle_voie") or "").strip()
    street = " ".join(p for p in (num, type_voie, voie) if p) or None
    return {
        "siren": etab.get("siren"),
        "siret": etab.get("siret"),
        "is_headquarters": bool(etab.get("is_siege")),
        "street": street,
        "postal_code": etab.get("code_postal"),
        "city": etab.get("libelle_commune"),
        "code_commune": etab.get("code_commune"),
        "status": "active" if etab.get("etat") == "A" else "closed",
        "naf": etab.get("naf"),
        "denomination": etab.get("denomination"),
        "lambert_x": etab.get("lambert_x"),
        "lambert_y": etab.get("lambert_y"),
    }


def headquarters_addresses(sirens: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Adresses de siège normalisées {siren: {street, postal_code, city, status…}}
    en UNE requête. Remplace l'enrichissement par table PG `sirene_sieges` dupliquée.
    """
    return {siren: _normalize_address(etab) for siren, etab in lookup_sieges(sirens).items()}


def list_establishments(siren: str, active_only: bool = True) -> list[dict[str, Any]]:
    """Liste tous les établissements d'un SIREN (sièges + secondaires).

    Args:
        siren: 9 chiffres
        active_only: filtre etatAdministratif = 'A'
    """
    where = ["siren = ?"]
    params: list[Any] = [siren]
    if active_only:
        where.append("etatAdministratifEtablissement = 'A'")
    sql = (
        f"SELECT {_SELECT_CLAUSE} FROM {_from_parquet()} "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY etablissementSiege DESC, dateDebut DESC NULLS LAST"
    )
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    cols = _output_columns()
    return [_row_to_dict(r, cols) for r in rows]


def lookup_siret(siret: str) -> Optional[dict[str, Any]]:
    """Renvoie un établissement précis par SIRET (14 chiffres)."""
    sql = (
        f"SELECT {_SELECT_CLAUSE} FROM {_from_parquet()} "
        "WHERE siret = ? LIMIT 1"
    )
    with _connect() as conn:
        row = conn.execute(sql, [siret]).fetchone()
    return _row_to_dict(row, _output_columns()) if row else None


def search(
    naf: Optional[str] = None,
    code_commune: Optional[str] = None,
    code_postal: Optional[str] = None,
    departement: Optional[str] = None,
    denomination: Optional[str] = None,
    enseigne: Optional[str] = None,
    active_only: bool = True,
    sieges_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Recherche multi-critères. Tous les filtres sont AND.

    Args:
        naf: code APE (ex. "4711F", "10.71C") — match exact sur activitePrincipale
        code_commune: code INSEE COG (ex. "13201" pour Marseille 1er)
        code_postal: ex. "13001"
        departement: code département 2 chars métropole (ex. "26") ou 3 chars DOM
            (ex. "971"). Match sur le préfixe du code postal.
        denomination: substring case-insensitive sur denomination ou enseigne
        enseigne: substring case-insensitive sur enseigne1/2/3
        active_only: filtre etat='A'
        sieges_only: ne renvoie que les sièges
        limit: max 1000
        offset: pagination
    """
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    where = ["1=1"]
    params: list[Any] = []

    if active_only:
        where.append("etatAdministratifEtablissement = 'A'")
    if sieges_only:
        where.append("etablissementSiege = TRUE")
    if naf:
        where.append("activitePrincipaleEtablissement = ?")
        params.append(naf)
    if code_commune:
        where.append("codeCommuneEtablissement = ?")
        params.append(code_commune)
    if code_postal:
        where.append("codePostalEtablissement = ?")
        params.append(code_postal)
    if departement:
        where.append("LEFT(codePostalEtablissement, ?) = ?")
        params.extend([len(departement), departement])
    if denomination:
        where.append("LOWER(denominationUsuelleEtablissement) LIKE ?")
        params.append(f"%{denomination.lower()}%")
    if enseigne:
        where.append(
            "(LOWER(enseigne1Etablissement) LIKE ? OR "
            " LOWER(enseigne2Etablissement) LIKE ? OR "
            " LOWER(enseigne3Etablissement) LIKE ?)"
        )
        like = f"%{enseigne.lower()}%"
        params.extend([like, like, like])

    sql = (
        f"SELECT {_SELECT_CLAUSE} FROM {_from_parquet()} "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY siret "
        "LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    cols = _output_columns()
    return [_row_to_dict(r, cols) for r in rows]


def count_active() -> int:
    """Comptage rapide pour sanity check / monitoring."""
    sql = f"SELECT COUNT(*) FROM {_from_parquet()} WHERE etatAdministratifEtablissement = 'A'"
    with _connect() as conn:
        return int(conn.execute(sql).fetchone()[0])


def parquet_info() -> dict[str, Any]:
    """Métadonnées pour healthcheck : taille fichier, dernière modif, count."""
    path = parquet_path()
    info: dict[str, Any] = {"path": path}
    if _is_remote(path):
        info["remote"] = True
    else:
        try:
            st = os.stat(path)
            info["size_bytes"] = st.st_size
            info["mtime"] = st.st_mtime
        except FileNotFoundError:
            info["error"] = "not_found"
            return info
    try:
        info["total_rows"] = int(
            _connect().execute(f"SELECT COUNT(*) FROM {_from_parquet()}").fetchone()[0]
        )
    except Exception as e:
        info["query_error"] = str(e)
    return info
