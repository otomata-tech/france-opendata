"""Ingestion BOAMP : crawl du dump XML DILA → parquet (lu ensuite par `boamp.BoampClient`).

Le portail OpenDataSoft de la DILA étant bloqué depuis les IP datacenter (issue #3), on
lit le dump de fichiers brut `echanges.dila.gouv.fr/OPENDATA/BOAMP/{année}/{mois}/{jour}/`
(un XML par avis, `<idweb>.xml`), joignable depuis datacenter. Chaque avis est parsé
(`boamp.parse_avis`, durci defusedxml) puis l'ensemble est écrit en parquet local
(DuckDB, compression ZSTD). Le push vers S3 est laissé au script de déploiement (cf.
`refresh_boamp_s3.py`), comme pour `sirene_stock`.

Pattern d'usage :
    build_parquet("/tmp/boamp.parquet", years=[2025, 2026])           # plein
    build_parquet("/opt/.../boamp.parquet", since="2026-06-01")       # incrémental (merge)

Nécessite l'extra `france-opendata[stock]` (duckdb + defusedxml).
"""
from __future__ import annotations

import concurrent.futures as _cf
import datetime as _dt
import os
import re
from typing import Any, Iterable, Optional

import requests

from .boamp import COLUMNS, parse_avis

BASE_URL = "https://echanges.dila.gouv.fr/OPENDATA/BOAMP"

_HREF_RE = re.compile(r'<a href="([^"?][^"]*)"')
_AVIS_RE = re.compile(r"^\d{2}-\d+\.xml$")
_DAY_RE = re.compile(r"^\d{2}/$")
_MONTH_RE = re.compile(r"^\d{2}/$")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "france-opendata/boamp-ingest (+https://github.com/otomata-tech/france-opendata)"
    return s


def _list_links(sess: requests.Session, url: str, timeout: int = 30) -> list[str]:
    """Liens d'un index Apache (hors tri/parent). Retourne les hrefs relatifs."""
    resp = sess.get(url, timeout=timeout)
    resp.raise_for_status()
    return _HREF_RE.findall(resp.text)


def iter_avis_urls(
    sess: requests.Session,
    years: Iterable[int],
    since: Optional[str] = None,
) -> list[str]:
    """Liste toutes les URLs d'avis XML pour les années données.

    Args:
        years: années à crawler (ex. [2025, 2026]).
        since: borne basse "YYYY-MM-DD" (incrémental) ; un jour est retenu si sa date
            calendaire >= since. Réduit le crawl aux mois/jours pertinents.
    """
    since_date = _dt.date.fromisoformat(since) if since else None
    urls: list[str] = []
    for year in years:
        year_url = f"{BASE_URL}/{year}/"
        try:
            months = [m for m in _list_links(sess, year_url) if _MONTH_RE.match(m)]
        except requests.RequestException:
            continue
        for month in sorted(months):
            mm = month.strip("/")
            if since_date and (year, int(mm)) < (since_date.year, since_date.month):
                continue
            month_url = f"{year_url}{month}"
            try:
                days = [d for d in _list_links(sess, month_url) if _DAY_RE.match(d)]
            except requests.RequestException:
                continue
            for day in sorted(days):
                dd = day.strip("/")
                try:
                    day_date = _dt.date(year, int(mm), int(dd))
                except ValueError:
                    continue
                if since_date and day_date < since_date:
                    continue
                day_url = f"{month_url}{day}"
                try:
                    files = [f for f in _list_links(sess, day_url) if _AVIS_RE.match(f)]
                except requests.RequestException:
                    continue
                urls.extend(f"{day_url}{f}" for f in files)
    return urls


def _fetch_row(sess: requests.Session, url: str, timeout: int = 30) -> Optional[dict[str, Any]]:
    try:
        resp = sess.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return parse_avis(resp.content, url)


def fetch_rows(urls: list[str], max_workers: int = 16) -> list[dict[str, Any]]:
    """Télécharge + parse les avis en parallèle. Les avis illisibles sont ignorés."""
    sess = _session()
    rows: list[dict[str, Any]] = []
    with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_fetch_row, sess, u) for u in urls]
        for fut in _cf.as_completed(futures):
            row = fut.result()
            if row is not None:
                rows.append(row)
    return rows


_DDL = (
    "CREATE TABLE boamp ("
    "idweb VARCHAR, annee INTEGER, objet VARCHAR, organisme VARCHAR, "
    "date_publication VARCHAR, date_limite_reponse VARCHAR, date_fin_diffusion VARCHAR, "
    "dep_publication VARCHAR, nature_marche VARCHAR, type_procedure VARCHAR, "
    "type_avis_nature VARCHAR, type_avis_famille VARCHAR, statut VARCHAR, "
    "descripteurs_libelle VARCHAR, descripteurs_json VARCHAR, synthese VARCHAR, url VARCHAR)"
)


def write_parquet(rows: list[dict[str, Any]], out_path: str, merge_existing: bool = True) -> int:
    """Écrit les lignes en parquet (DuckDB, ZSTD). Si merge_existing et qu'un parquet
    local existe, fusionne en dédupliquant par idweb (les NOUVELLES lignes priment).
    Retourne le nombre total de lignes écrites."""
    import duckdb  # extra [stock]

    con = duckdb.connect(database=":memory:")
    con.execute(_DDL)
    if rows:
        placeholders = ", ".join("?" * len(COLUMNS))
        con.executemany(
            f"INSERT INTO boamp VALUES ({placeholders})",
            [tuple(r.get(c) for c in COLUMNS) for r in rows],
        )

    select = ", ".join(COLUMNS)
    if merge_existing and os.path.exists(out_path):
        # Union : anciennes lignes non resoumises + nouvelles (priorité aux nouvelles).
        new_ids = con.execute("SELECT DISTINCT idweb FROM boamp").fetchall()
        ids = {r[0] for r in new_ids}
        con.execute(
            f"CREATE TABLE old AS SELECT {select} FROM read_parquet('{out_path}')"
        )
        if ids:
            placeholders = ", ".join("?" * len(ids))
            con.execute(
                f"DELETE FROM old WHERE idweb IN ({placeholders})", list(ids)
            )
        con.execute("INSERT INTO boamp SELECT * FROM old")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    con.execute(
        f"COPY (SELECT {select} FROM boamp ORDER BY date_publication DESC NULLS LAST, idweb DESC) "
        f"TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    total = int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{out_path}')").fetchone()[0])
    con.close()
    return total


def build_parquet(
    out_path: str,
    *,
    years: Optional[Iterable[int]] = None,
    since: Optional[str] = None,
    max_workers: int = 16,
    limit: Optional[int] = None,
    merge_existing: bool = True,
) -> dict[str, Any]:
    """Crawl + parse + écrit le parquet. Renvoie un résumé {urls, parsed, total_rows}.

    Args:
        out_path: chemin parquet local de sortie.
        years: années à crawler (défaut : année courante + précédente — fenêtre 2 ans).
        since: borne basse "YYYY-MM-DD" pour un crawl incrémental.
        limit: plafond d'URLs (debug/test).
        merge_existing: fusionne avec un parquet local existant (dédup par idweb).
    """
    if years is None:
        this_year = _dt.date.fromisoformat(since).year if since else _today_year()
        years = sorted({this_year, this_year - 1})
    sess = _session()
    urls = iter_avis_urls(sess, years, since=since)
    if limit:
        urls = urls[:limit]
    rows = fetch_rows(urls, max_workers=max_workers)
    total = write_parquet(rows, out_path, merge_existing=merge_existing)
    return {"urls": len(urls), "parsed": len(rows), "total_rows": total, "out_path": out_path}


def _today_year() -> int:
    # Isolé pour rester testable (pas de Date.now() figé dans la logique de crawl).
    return _dt.date.today().year


def _main(argv: Optional[list[str]] = None) -> int:
    """CLI d'ingestion (utilisée par le script de refresh S3).

    Exemples :
        python -m france_opendata.boamp_ingest out.parquet                  # plein, 2 ans
        python -m france_opendata.boamp_ingest out.parquet --years 2025 2026
        python -m france_opendata.boamp_ingest out.parquet --since 2026-06-17 --merge
    """
    import argparse
    import json as _json

    p = argparse.ArgumentParser(prog="boamp_ingest", description="Ingestion BOAMP → parquet")
    p.add_argument("out_path", help="chemin parquet local de sortie")
    p.add_argument("--years", type=int, nargs="*", help="années à crawler (défaut : 2 ans glissants)")
    p.add_argument("--since", help="borne basse YYYY-MM-DD (crawl incrémental)")
    p.add_argument("--merge", action="store_true", help="fusionner avec le parquet existant (dédup idweb)")
    p.add_argument("--no-merge", dest="merge", action="store_false")
    p.add_argument("--max-workers", type=int, default=16)
    p.add_argument("--limit", type=int, default=None, help="plafond d'URLs (debug)")
    p.set_defaults(merge=False)
    args = p.parse_args(argv)

    res = build_parquet(
        args.out_path,
        years=args.years or None,
        since=args.since,
        max_workers=args.max_workers,
        limit=args.limit,
        merge_existing=args.merge,
    )
    print(_json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
