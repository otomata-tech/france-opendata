"""INPI/BCE — bilans & ratios financiers depuis la liasse fiscale (open data).

L'ancien `InpiClient` interrogeait `ratios_inpi_bce` sur `data.economie.gouv.fr`
(OpenDataSoft), **bloqué depuis les IP datacenter** (timeout TCP, anti-scraping) —
même cause que BOAMP (#3). Le dataset officiel des ratios ne sauve pas (ses ressources
pointent sur le même host ODS). → on lit le parquet **« Données financières détaillées
des entreprises »** (Signaux Faibles, 6,4 M lignes, joignable datacenter) via DuckDB
httpfs, et on en dérive CA / résultat net.

⚠️ **Périmètre actuel (socle, issue #4)** : le parquet expose les **postes BRUTS de la
liasse** (codes de cases CERFA 2050-2053 réel normal / 2033 simplifié), PAS les ratios
pré-calculés BdF. Ce client renvoie donc :
- `chiffre_d_affaires`, `resultat_net` (dérivés, validés en réel normal) ;
- `liasse` : tous les postes bruts `{code: valeur}` (l'agent/consommateur décide).
Les **ratios complets** (EBE/EBIT/marges/autonomie/endettement/liquidité…), notamment
le mapping **simplifié 2033**, sont **différés** à une passe dédiée validée (un code
faux = chiffre faux en silence). cf. issue #4.

Chemin du parquet via `INPI_BILAN_PARQUET_PATH` (défaut : URL stable data.gouv, redirect
suivi par DuckDB → MAJ mensuelle automatique). Origines : `https://` (défaut, public),
`s3://` (creds `INPI_BILAN_S3_*`), ou chemin local. Import lazy `duckdb` (extra [stock]).

API publique inchangée : `list_exercises(siren)` / `get_bilan(siren, date_cloture)`.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Optional

# Valeur sentinelle « poste manquant / masqué » dans le parquet (INT32_MAX), à traiter
# comme NULL (sinon les agrégats explosent — vérifié sur des bilans réels).
_MISSING = 2147483647

DEFAULT_PARQUET_URL = "https://www.data.gouv.fr/api/1/datasets/r/c4ac8f98-2c97-4417-9070-0cbb9de03875"

# Préférence de bilan quand plusieurs existent pour un même (siren, date_cloture) :
# comptes sociaux (réel normal C) > simplifié S > consolidé K.
_TYPE_RANK = {"C": 0, "S": 1, "K": 2}

# Codes CERFA du CA. Réel normal (2052) : FL = chiffre d'affaires nets (total).
# Simplifié (2033-B) : pas de case « CA total » unique → somme ventes + production
# (marchandises/biens/services, France + export), net.
_CA_CODE_RN = "FL"
_CA_CODES_S = ("209", "210", "214", "215", "217", "218")
# Résultat net. Réel normal : HN (2053, bénéfice/perte) avec repli DI (2051, passif).
_RN_CODES_RN = ("HN", "DI")


def parquet_path() -> str:
    return os.environ.get("INPI_BILAN_PARQUET_PATH", DEFAULT_PARQUET_URL)


def _is_remote(path: str) -> bool:
    return path.startswith(("s3://", "http://", "https://"))


def _env(name: str) -> Optional[str]:
    return os.environ.get(f"INPI_BILAN_S3_{name}") or os.environ.get(f"SIRENE_STOCK_S3_{name}")


def _configure_remote(conn, path: str) -> None:
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute("SET enable_http_metadata_cache=true;")
    conn.execute("SET enable_object_cache=true;")
    if not path.startswith("s3://"):
        return  # http(s) public : aucun credential
    endpoint, key, secret = _env("ENDPOINT"), _env("KEY_ID"), _env("SECRET")
    if not (endpoint and key and secret):
        raise RuntimeError(
            "Parquet S3 distant : définir INPI_BILAN_S3_{ENDPOINT,KEY_ID,SECRET} "
            "(ou les SIRENE_STOCK_S3_* du même bucket)."
        )
    region = _env("REGION") or "fr-par"
    url_style = _env("URL_STYLE") or "vhost"
    ep = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    def _q(v: str) -> str:
        return v.replace("'", "''")
    conn.execute(
        "CREATE OR REPLACE SECRET inpi_s3 (TYPE S3, "
        f"KEY_ID '{_q(key)}', SECRET '{_q(secret)}', ENDPOINT '{_q(ep)}', "
        f"REGION '{_q(region)}', URL_STYLE '{_q(url_style)}', USE_SSL true)"
    )


class InpiClient:
    def __init__(self, parquet_path: Optional[str] = None, timeout: Any = None):
        # `timeout` conservé pour compat de signature (lecture parquet, plus d'appel ODS).
        self._path = parquet_path or globals()["parquet_path"]()

    def _connect(self):
        import duckdb  # extra [stock] ; lazy pour ne pas casser sans l'extra
        conn = duckdb.connect(database=":memory:", read_only=False)
        if _is_remote(self._path):
            _configure_remote(conn, self._path)
        return conn

    def _from(self) -> str:
        return f"read_parquet('{self._path}')"

    @staticmethod
    def _clean(liasse: dict) -> dict[str, int]:
        """MAP brute → {code: valeur}, sentinelle/none retirées."""
        return {k: v for k, v in (liasse or {}).items() if v is not None and v != _MISSING}

    @staticmethod
    def _ca(type_bilan: str, postes: dict[str, int]) -> Optional[int]:
        if type_bilan in ("C", "K"):
            return postes.get(_CA_CODE_RN)
        if type_bilan == "S":
            vals = [postes[c] for c in _CA_CODES_S if c in postes]
            return sum(vals) if vals else None
        return None

    @staticmethod
    def _resultat_net(type_bilan: str, postes: dict[str, int]) -> Optional[int]:
        if type_bilan in ("C", "K"):
            for c in _RN_CODES_RN:
                if c in postes:
                    return postes[c]
        return None  # simplifié : code 2033 non encore validé (différé, #4)

    def list_exercises(self, siren: str) -> list[dict[str, Any]]:
        """Exercices disponibles pour un SIREN (récent d'abord).

        Renvoie {siren, date_cloture_exercice, type_bilan, confidentiality,
        chiffre_d_affaires}. Si plusieurs bilans à la même date, garde le préféré
        (réel normal > simplifié > consolidé)."""
        sql = (
            "SELECT siren, CAST(date_cloture_exercice AS VARCHAR) AS date_cloture_exercice, "
            "type_bilan, confidentiality, liasse "
            f"FROM {self._from()} WHERE siren = ? "
            "ORDER BY date_cloture_exercice DESC"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, [siren]).fetchall()
            cols = [d[0] for d in conn.description]
        best: dict[str, dict] = {}
        for r in rows:
            d = dict(zip(cols, r))
            postes = self._clean(d.pop("liasse"))
            d["chiffre_d_affaires"] = self._ca(d["type_bilan"], postes)
            key = d["date_cloture_exercice"]
            if key not in best or _TYPE_RANK.get(d["type_bilan"], 9) < _TYPE_RANK.get(best[key]["type_bilan"], 9):
                best[key] = d
        return sorted(best.values(), key=lambda x: x["date_cloture_exercice"], reverse=True)

    def get_bilan(self, siren: str, date_cloture: str) -> Optional[dict[str, Any]]:
        """Un bilan par SIREN + date de clôture (YYYY-MM-DD).

        Renvoie métadonnées + chiffre_d_affaires + resultat_net (dérivés) + `liasse`
        (tous les postes bruts code→valeur, sentinelle retirée). Préfère le bilan
        réel normal si plusieurs types à la même date."""
        sql = (
            "SELECT siren, CAST(date_cloture_exercice AS VARCHAR) AS date_cloture_exercice, "
            "type_bilan, confidentiality, liasse "
            f"FROM {self._from()} WHERE siren = ? AND CAST(date_cloture_exercice AS VARCHAR) = ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, [siren, date_cloture]).fetchall()
            cols = [d[0] for d in conn.description]
        if not rows:
            return None
        recs = [dict(zip(cols, r)) for r in rows]
        recs.sort(key=lambda x: _TYPE_RANK.get(x["type_bilan"], 9))
        rec = recs[0]
        postes = self._clean(rec.pop("liasse"))
        rec["chiffre_d_affaires"] = self._ca(rec["type_bilan"], postes)
        rec["resultat_net"] = self._resultat_net(rec["type_bilan"], postes)
        rec["liasse"] = postes
        return rec

    def info(self) -> dict[str, Any]:
        """Healthcheck : chemin + comptage."""
        info: dict[str, Any] = {"path": self._path}
        try:
            with self._connect() as conn:
                info["total_rows"] = int(conn.execute(f"SELECT COUNT(*) FROM {self._from()}").fetchone()[0])
        except Exception as e:  # noqa: BLE001
            info["error"] = str(e)
        return info
