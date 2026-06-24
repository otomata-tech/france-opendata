"""BOAMP (Bulletin Officiel des Annonces de Marchés Publics) — avis de marchés publics.

Le portail OpenDataSoft de la DILA (`boamp-datadila.opendatasoft.com`) est **bloqué
depuis les IP datacenter** (anti-scraping côté OpenDataSoft, timeout TCP avant TLS) —
voir issue france-opendata#3. À la place, on lit le **dump XML brut de la DILA**
(`echanges.dila.gouv.fr/OPENDATA/BOAMP/`, joignable depuis datacenter), pré-agrégé en
**parquet** par un job d'ingestion (`boamp_ingest`), puis interrogé via **DuckDB** —
exactement le pattern de `sirene_stock`.

Le parquet est résolu via `BOAMP_STOCK_PARQUET_PATH` (défaut
`/opt/oto-mcp/data/boamp/boamp.parquet`). Trois sources : chemin local, `s3://…`
(httpfs, creds `BOAMP_STOCK_S3_*` avec repli sur `SIRENE_STOCK_S3_*` — même bucket),
ou `https://…` public.

L'API publique (`search`, `get`) est INCHANGÉE par rapport à la version OpenDataSoft :
les consommateurs (oto-mcp `fr.py`, oto-cli) ne bougent pas.

Nécessite l'extra `france-opendata[stock]` (duckdb) — import lazy pour ne pas casser
`import france_opendata` sans l'extra.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Optional
from xml.etree import ElementTree as ET


DEFAULT_PATH = "/opt/oto-mcp/data/boamp/boamp.parquet"

# Marchés : valeurs canoniques (balise enfant en majuscules, cf. parse_avis).
MARKET_TYPES = ("TRAVAUX", "FOURNITURES", "SERVICES")


def parquet_path() -> str:
    return os.environ.get("BOAMP_STOCK_PARQUET_PATH", DEFAULT_PATH)


# ---------------------------------------------------------------------------
# Parsing d'un avis BOAMP (XML DILA v3.x) → dict plat de colonnes parquet.
# Réutilisé par l'ingestion (boamp_ingest) ET disponible pour tout reparse.
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    """Localname sans namespace ({uri}Tag → Tag)."""
    return tag.rsplit("}", 1)[-1]


def _find(elem: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    if elem is None:
        return None
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _text(elem: Optional[ET.Element], name: str) -> Optional[str]:
    node = _find(elem, name)
    if node is None or node.text is None:
        return None
    val = node.text.strip()
    return val or None


def _enum(elem: Optional[ET.Element], name: str) -> Optional[str]:
    """Champ « enum » BOAMP : la VALEUR est le nom de l'unique balise enfant
    (ex. <NATURE_MARCHE><SERVICES/></NATURE_MARCHE> → "SERVICES")."""
    node = _find(elem, name)
    if node is None:
        return None
    for child in node:
        return _local(child.tag)
    return None


def _strip_html(html: Optional[str]) -> Optional[str]:
    if not html:
        return None
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def parse_avis(xml_bytes: bytes, url: str = "") -> Optional[dict[str, Any]]:
    """Parse un avis BOAMP (bytes XML) → dict de colonnes, ou None si illisible.

    Parsing durci via defusedxml (XXE / billion-laughs) — les avis sont déposés par
    des tiers, donc traités comme du contenu non fiable.
    """
    from defusedxml.ElementTree import fromstring as _safe_fromstring  # extra [stock]
    try:
        root = _safe_fromstring(xml_bytes)
    except Exception:  # noqa: BLE001 — ParseError, EntitiesForbidden, etc. → avis ignoré
        return None

    gestion = _find(root, "GESTION")
    reference = _find(gestion, "REFERENCE")
    indexation = _find(gestion, "INDEXATION")
    type_avis = _find(reference, "TYPE_AVIS")

    idweb = _text(reference, "IDWEB")
    if not idweb:
        return None

    descripteurs: list[dict[str, str]] = []
    desc_parent = _find(indexation, "DESCRIPTEURS")
    if desc_parent is not None:
        for d in desc_parent:
            if _local(d.tag) != "DESCRIPTEUR":
                continue
            descripteurs.append({
                "code": _text(d, "CODE") or "",
                "libelle": _text(d, "LIBELLE") or "",
            })
    descripteurs_libelle = " | ".join(d["libelle"] for d in descripteurs if d["libelle"])

    date_pub = _text(indexation, "DATE_PUBLICATION")
    annee = None
    if date_pub and len(date_pub) >= 4 and date_pub[:4].isdigit():
        annee = int(date_pub[:4])

    synthese = _strip_html((_find(root, "HTMLSYNTHESE") is not None
                            and ET.tostring(_find(root, "HTMLSYNTHESE"),
                                            encoding="unicode")) or None)

    return {
        "idweb": idweb,
        "annee": annee,
        "objet": _text(indexation, "RESUME_OBJET"),
        "organisme": _text(indexation, "NOMORGANISME"),
        "date_publication": date_pub,
        "date_limite_reponse": _text(indexation, "DATE_LIMITE_REPONSE"),
        "date_fin_diffusion": _text(indexation, "DATE_FIN_DIFFUSION"),
        "dep_publication": _text(indexation, "DEP_PUBLICATION"),
        "nature_marche": _enum(indexation, "NATURE_MARCHE"),
        "type_procedure": _enum(indexation, "TYPE_PROCEDURE"),
        "type_avis_nature": _enum(type_avis, "NATURE"),
        "type_avis_famille": _enum(type_avis, "FAMILLE"),
        "statut": _enum(type_avis, "STATUT"),
        "descripteurs_libelle": descripteurs_libelle or None,
        "descripteurs_json": json.dumps(descripteurs, ensure_ascii=False) if descripteurs else None,
        "synthese": synthese,
        "url": url or None,
    }


# Ordre des colonnes du parquet (source de vérité, partagée avec l'ingestion).
COLUMNS = [
    "idweb", "annee", "objet", "organisme",
    "date_publication", "date_limite_reponse", "date_fin_diffusion",
    "dep_publication", "nature_marche", "type_procedure",
    "type_avis_nature", "type_avis_famille", "statut",
    "descripteurs_libelle", "descripteurs_json", "synthese", "url",
]


# ---------------------------------------------------------------------------
# Lecture DuckDB du parquet (read-only, connexion à la demande).
# ---------------------------------------------------------------------------

def _is_remote(path: str) -> bool:
    return path.startswith(("s3://", "http://", "https://"))


def _env(name: str) -> Optional[str]:
    """Env BOAMP_STOCK_S3_* avec repli sur SIRENE_STOCK_S3_* (même bucket Scaleway)."""
    return os.environ.get(f"BOAMP_STOCK_S3_{name}") or os.environ.get(f"SIRENE_STOCK_S3_{name}")


def _configure_remote(conn, path: str) -> None:
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("SET enable_http_metadata_cache=true;")
    conn.execute("SET enable_object_cache=true;")
    if not path.startswith("s3://"):
        return
    endpoint, key, secret = _env("ENDPOINT"), _env("KEY_ID"), _env("SECRET")
    if not (endpoint and key and secret):
        raise RuntimeError(
            "Parquet S3 distant : définir BOAMP_STOCK_S3_{ENDPOINT,KEY_ID,SECRET} "
            "(ou les SIRENE_STOCK_S3_* du même bucket)."
        )
    region = _env("REGION") or "fr-par"
    url_style = _env("URL_STYLE") or "vhost"
    ep = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    def _q(v: str) -> str:
        return v.replace("'", "''")
    conn.execute(
        "CREATE OR REPLACE SECRET boamp_s3 (TYPE S3, "
        f"KEY_ID '{_q(key)}', SECRET '{_q(secret)}', ENDPOINT '{_q(ep)}', "
        f"REGION '{_q(region)}', URL_STYLE '{_q(url_style)}', USE_SSL true)"
    )


class BoampClient:
    """Client de recherche d'avis BOAMP sur le parquet (DuckDB)."""

    def __init__(self, parquet_path: Optional[str] = None, timeout: int = 30):
        # `timeout` conservé pour compat de signature (n'est plus utilisé : lecture
        # parquet locale/S3, plus d'appel HTTP synchrone à la recherche).
        self._path = parquet_path or parquet_path_env()
        self.timeout = timeout

    def _connect(self):
        import duckdb  # extra [stock] ; lazy pour ne pas casser sans l'extra
        conn = duckdb.connect(database=":memory:", read_only=False)
        if _is_remote(self._path):
            _configure_remote(conn, self._path)
        return conn

    def _from(self) -> str:
        return f"read_parquet('{self._path}')"

    @staticmethod
    def _row_to_dict(row: tuple, cols: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for col, val in zip(cols, row):
            if val == "" or val is None:
                out[col] = None
            elif isinstance(val, (_dt.datetime, _dt.date)):
                out[col] = val.isoformat()
            else:
                out[col] = val
        if out.get("descripteurs_json"):
            try:
                out["descripteurs"] = json.loads(out.pop("descripteurs_json"))
            except (ValueError, TypeError):
                out.pop("descripteurs_json", None)
        else:
            out.pop("descripteurs_json", None)
        return out

    def search(
        self,
        query: Optional[str] = None,
        descripteur: Optional[str] = None,
        departement: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        type_marche: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Recherche d'avis de marchés publics BOAMP. Tous les filtres sont AND.

        Returns {results, total_count}.
        """
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        where = ["1=1"]
        params: list[Any] = []
        if query:
            where.append("objet ILIKE ?")
            params.append(f"%{query}%")
        if descripteur:
            where.append("descripteurs_libelle ILIKE ?")
            params.append(f"%{descripteur}%")
        if departement:
            where.append("dep_publication = ?")
            params.append(departement)
        if date_from:
            where.append("date_publication >= ?")
            params.append(date_from)
        if date_to:
            where.append("date_publication <= ?")
            params.append(date_to)
        if type_marche:
            where.append("nature_marche = ?")
            params.append(type_marche.upper())

        clause = " AND ".join(where)
        select = ", ".join(COLUMNS)
        with self._connect() as conn:
            total = int(conn.execute(
                f"SELECT COUNT(*) FROM {self._from()} WHERE {clause}", params
            ).fetchone()[0])
            rows = conn.execute(
                f"SELECT {select} FROM {self._from()} WHERE {clause} "
                "ORDER BY date_publication DESC NULLS LAST, idweb DESC "
                "LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        return {
            "results": [self._row_to_dict(r, COLUMNS) for r in rows],
            "total_count": total,
        }

    def get(self, idweb: str) -> Optional[dict[str, Any]]:
        """Récupère un avis BOAMP par son idweb."""
        select = ", ".join(COLUMNS)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {select} FROM {self._from()} WHERE idweb = ? LIMIT 1",
                [idweb],
            ).fetchone()
        return self._row_to_dict(row, COLUMNS) if row else None

    def info(self) -> dict[str, Any]:
        """Métadonnées pour healthcheck : chemin, count, fenêtre de dates."""
        info: dict[str, Any] = {"path": self._path}
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*), MIN(date_publication), MAX(date_publication) "
                    f"FROM {self._from()}"
                ).fetchone()
            info["total_rows"] = int(row[0])
            info["date_min"], info["date_max"] = row[1], row[2]
        except Exception as e:  # noqa: BLE001 — healthcheck best-effort
            info["error"] = str(e)
        return info


# Alias module-level pour éviter le shadowing du paramètre `parquet_path`.
parquet_path_env = parquet_path
