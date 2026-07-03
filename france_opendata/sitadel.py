"""Sit@del — autorisations d'urbanisme (permis de construire / d'aménager), SDES DiDo.

Source : dataset DiDo « Liste des permis de construire et autres autorisations
d'urbanisme » (SDES, ministère de la Transition écologique). 4 datafiles à RID fixe :
  https://data.statistiques.developpement-durable.gouv.fr/dido/api/v1/datafiles/<rid>/csv
Pas de clé, Licence Ouverte, MAJ mensuelle, couverture France depuis 2013.

Deux surfaces d'accès :
- **`query()` — API DiDo `/rows`, filtrée côté serveur** (COMM, DEP_CODE, AN_DEPOT…,
  opérateurs eq/in/gte/lte, pagination pageSize∈{10,20,50,100}). Adaptée au **lookup
  ponctuel** (une commune / un département) : c'est le pendant « live » du productible
  solaire. Le schéma des lignes JSON est **identique aux colonnes du CSV** (mêmes
  parseurs) — seuls les types diffèrent (JSON natif int/bool vs strings du CSV).
- **`download()` + `iter_permis()` — CSV national COMPLET** (276 Mo pour les locaux),
  pour l'ingestion de MASSE. Pas de Range HTTP → un refresh re-télécharge tout, un
  download coupé repart de zéro.

Gotchas de la source (mesurés, cf. GR docs/sources/04-sitadel.md) :
- **Le RID est fixe** et renvoie toujours le dernier millésime publié ; le millésime
  courant se lit dans les métadonnées (`SitadelClient.metadata()["millesime"]`).
- **Stall silencieux observé** sur les gros fichiers (socket ouverte, plus d'octets,
  aucun event d'erreur) → le download borne le temps entre deux chunks (read-timeout)
  et retente le fichier entier en cas de coupure.
- **~35 % des permis sont anonymisés RGPD** (personnes physiques) : SIREN_DEM et
  DENOM_DEM vides. Ce n'est pas un trou de données, c'est la règle de diffusion.
- CSV `;`, quoted `"`, UTF-8. Champ libre OBJET_DAU absent : tout est codé.

Usage type (streaming, jamais le fichier en RAM) :
    client = SitadelClient()
    path = client.download(RID_LOGEMENTS, "/tmp/logements.csv")
    for permis in client.iter_permis(path, kind="logements", depts={"75", "92"}):
        ...
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

DIDO_BASE = "https://data.statistiques.developpement-durable.gouv.fr/dido/api/v1/datafiles"

# Les 4 datafiles du dataset (RID fixes, contenu = dernier millésime publié).
RID_LOGEMENTS = "8b35affb-55fc-4c1f-915b-7750f974446a"  # PC/DP créant des logements
RID_LOCAUX = "f8f0700f-806c-40a7-83b1-f21cf507e7c4"     # PC locaux non résidentiels
RID_AMENAGER = "96883f50-538b-41f9-a059-c6eb97e6a23a"   # PA — lotissements, aménagements
RID_DEMOLIR = "1a9a2f0c-56fe-4e69-84a7-fbbda2121f02"    # PD — permis de démolir

KINDS = ("logements", "locaux", "amenager")

# Nomenclature ETAT_DAU/ETAT_PA (dictionnaire de variables SDES).
ETAT_DAU = {"2": "autorise", "4": "commence", "5": "termine", "6": "annule"}

# DESTINATION_PRINCIPALE (fichier locaux) — destinations du code de l'urbanisme.
# Codes 1/2/3/4/6/7/8/9 vérifiés sur le fichier réel (GR #88) ; 5 = artisanat (CERFA).
DESTINATIONS = {
    "1": "habitation",
    "2": "hébergement hôtelier",
    "3": "bureaux",
    "4": "commerce et activités de service",
    "5": "artisanat",
    "6": "industrie",
    "7": "exploitation agricole et forestière",
    "8": "entrepôt",
    "9": "équipements d'intérêt collectif et services publics",
}


def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    t = s.strip()
    return t or None


def _to_int(s: Optional[str]) -> Optional[int]:
    t = _clean(s)
    if t is None:
        return None
    try:
        return int(float(t.replace(",", ".")))
    except ValueError:
        return None


def _to_bool(s: Optional[str]) -> bool:
    return (_clean(s) or "").lower() == "true"


def _cadastre(row: dict) -> list[str]:
    out = []
    for i in (1, 2, 3):
        sec = _clean(row.get(f"SEC_CADASTRE{i}"))
        num = _clean(row.get(f"NUM_CADASTRE{i}"))
        if sec and num:
            out.append(f"{sec}-{num}")
    return out


def _tronc_commun(row: dict) -> dict[str, Any]:
    """Colonnes partagées par les 3 fichiers : identité du DAU, géo, dates, demandeur."""
    etat_brut = _clean(row.get("ETAT_DAU") or row.get("ETAT_PA"))
    return {
        "num_dau": _clean(row.get("NUM_DAU") or row.get("NUM_PA")),
        "type_dau": _clean(row.get("TYPE_DAU")) or ("PA" if "NUM_PA" in row else None),
        "etat": ETAT_DAU.get(etat_brut, etat_brut),
        "commune": _clean(row.get("COMM")),
        "dept": _clean(row.get("DEP_CODE")),
        "an_depot": _to_int(row.get("AN_DEPOT")),
        "date_autorisation": _clean(row.get("DATE_REELLE_AUTORISATION")),
        "date_ouverture_chantier": _clean(row.get("DATE_REELLE_DOC")),
        "date_achevement": _clean(row.get("DATE_REELLE_DAACT")),
        "demandeur": {
            "siren": _clean(row.get("SIREN_DEM")),
            "siret": _clean(row.get("SIRET_DEM")),
            "denomination": _clean(row.get("DENOM_DEM")),
            "ape": _clean(row.get("APE_DEM")),
            "categorie_juridique": _clean(row.get("CJ_DEM")),
        },
        "adresse": {
            "num": _clean(row.get("ADR_NUM_TER")),
            "voie": _clean(row.get("ADR_LIBVOIE_TER")),
            "lieudit": _clean(row.get("ADR_LIEUDIT_TER")),
            "code_postal": _clean(row.get("ADR_CODPOST_TER")),
            "cadastre": _cadastre(row),
        },
        "superficie_terrain_m2": _to_int(row.get("SUPERFICIE_TERRAIN")),
        "rec_archi": _to_bool(row.get("REC_ARCHI")),
        "zone_op": _clean(row.get("ZONE_OP")),
    }


def parse_logement(row: dict) -> dict[str, Any]:
    """Fichier « logements » : PC/DP créant des logements (le cœur promoteur)."""
    p = _tronc_commun(row)
    p.update({
        "nature_projet": _clean(row.get("NATURE_PROJET_DECLAREE")),  # 1=neuf, 2=existant
        "i_extension": _to_bool(row.get("I_EXTENSION")),
        "i_surelevation": _to_bool(row.get("I_SURELEVATION")),
        "nb_niveaux_max": _to_int(row.get("NB_NIV_MAX")),
        "residence_service": _clean(row.get("RESIDENCE")),  # code type résidence (9 = non)
        "logements": {
            "crees_total": _to_int(row.get("NB_LGT_TOT_CREES")) or 0,
            "crees_individuels": _to_int(row.get("NB_LGT_IND_CREES")) or 0,
            "crees_collectifs": _to_int(row.get("NB_LGT_COL_CREES")) or 0,
            "demolis": _to_int(row.get("NB_LGT_DEMOLIS")) or 0,
            "locatif_social": _to_int(row.get("NB_LGT_PRET_LOC_SOCIAL")) or 0,
            "accession_sociale_hors_ptz": _to_int(row.get("NB_LGT_ACC_SOC_HORS_PTZ")) or 0,
            "ptz": _to_int(row.get("NB_LGT_PTZ")) or 0,
        },
        "surf_hab_creee_m2": _to_int(row.get("SURF_HAB_CREEE")) or 0,
        "surf_hab_demolie_m2": _to_int(row.get("SURF_HAB_DEMOLIE")) or 0,
    })
    return p


def parse_locaux(row: dict) -> dict[str, Any]:
    """Fichier « locaux non résidentiels » : PC créant des locaux (bureaux, commerce…).

    `sp_finale_estimee_m2` = existant - démoli + créé + issu de transformation
    (SURF_LOC_TRANSFORMEE = transfo in-place, non additionnée — cf. GR §7.8 :
    prendre SURF_LOC_CREEE seul sous-estime ×4 sur les extensions/phases multiples).
    """
    p = _tronc_commun(row)
    avant = _to_int(row.get("SURF_LOC_AVANT")) or 0
    creee = _to_int(row.get("SURF_LOC_CREEE")) or 0
    transfo = _to_int(row.get("SURF_LOC_ISSUE_TRANSFO")) or 0
    demolie = _to_int(row.get("SURF_LOC_DEMOLIE")) or 0
    dest = _clean(row.get("DESTINATION_PRINCIPALE"))
    p.update({
        "nature_projet": _clean(row.get("NATURE_PROJET_DECLAREE")),
        "i_extension": _to_bool(row.get("I_EXTENSION")),
        "i_surelevation": _to_bool(row.get("I_SURELEVATION")),
        "destination_principale": dest,
        "destination_libelle": DESTINATIONS.get(dest) if dest else None,
        "surf_loc_creee_m2": creee,
        "surf_loc_demolie_m2": demolie,
        "sp_finale_estimee_m2": max(0, avant - demolie) + creee + transfo,
    })
    return p


def parse_amenager(row: dict) -> dict[str, Any]:
    """Fichier « permis d'aménager » : lotissements, gros aménagements (35 colonnes)."""
    return _tronc_commun(row)


_PARSERS = {"logements": parse_logement, "locaux": parse_locaux, "amenager": parse_amenager}

RID_BY_KIND = {"logements": RID_LOGEMENTS, "locaux": RID_LOCAUX, "amenager": RID_AMENAGER}

# Pagination DiDo : pageSize est contraint à une de ces valeurs (400 sinon).
DIDO_PAGE_SIZES = (10, 20, 50, 100)

# Opérateurs de filtre DiDo (`champ=op:valeur`). `in` = liste jointe par des virgules.
_DIDO_OPS = frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "in"})


def _dido_filter(spec: Any) -> str:
    """Traduit une spec de filtre → syntaxe DiDo `op:valeur`.

    spec = valeur brute (→ `eq:valeur`) OU tuple `(op, valeur)` avec op ∈ _DIDO_OPS ;
    pour `in`, valeur = itérable → jointe par des virgules (`in:a,b,c`).
    """
    if isinstance(spec, tuple) and len(spec) == 2 and spec[0] in _DIDO_OPS:
        op, val = spec
        if op == "in":
            items = val if isinstance(val, (list, tuple, set)) else [val]
            val = ",".join(str(v) for v in items)
        return f"{op}:{val}"
    return f"eq:{spec}"


def _stringify(row: dict) -> dict[str, Optional[str]]:
    """Ligne JSON DiDo → dict de strings (None préservé), pour réutiliser les parseurs
    CSV tels quels : `/rows` renvoie des types natifs (int/bool), les parseurs
    attendent des strings (ils passent tout par `_clean`/`_to_int`/`_to_bool`)."""
    return {k: (None if v is None else v if isinstance(v, str) else str(v)) for k, v in row.items()}


class SitadelClient:
    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self.session = requests.Session()

    def metadata(self, rid: str) -> dict[str, Any]:
        """Métadonnées DiDo du datafile : titre, `millesime` (AAAA-MM), couverture."""
        r = self.session.get(f"{DIDO_BASE}/{rid}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def download(self, rid: str, dest: str | Path, retries: int = 5) -> Path:
        """Télécharge le CSV complet sur disque (streaming, jamais en RAM).

        Le read-timeout s'applique entre deux chunks : il convertit le stall
        silencieux DiDo (socket ouverte, plus d'octets) en échec borné, puis on
        retente le fichier entier (pas de Range côté DiDo → reprise impossible).
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{DIDO_BASE}/{rid}/csv"
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                with self.session.get(url, stream=True, timeout=(30, self.timeout)) as r:
                    r.raise_for_status()
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1 << 20):
                            f.write(chunk)
                    tmp.replace(dest)
                return dest
            except (requests.RequestException, OSError) as e:
                last_err = e
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"download Sit@del {rid} en échec après {retries} tentatives: {last_err}")

    def query(
        self,
        rid: str,
        kind: str,
        *,
        filters: Optional[dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """Interroge l'API DiDo `/rows` (filtre + pagination côté serveur) → permis normalisés.

        Pendant « live » de `download()`+`iter_permis()` : au lieu de rapatrier le CSV
        national, DiDo filtre côté serveur. Adapté au lookup ponctuel (une commune / un
        département), pas au refresh de masse (pagination bornée à pageSize≤100).

        Args:
            rid: datafile (RID_LOGEMENTS / RID_LOCAUX / RID_AMENAGER).
            kind: sélectionne le parseur ("logements" | "locaux" | "amenager").
            filters: {champ: spec} — spec = valeur (→ `eq`) ou tuple `(op, valeur)`,
                op ∈ {eq,neq,gt,gte,lt,lte,in}. Champs utiles : COMM (INSEE commune),
                DEP_CODE (département), AN_DEPOT (année de dépôt), ETAT_DAU,
                DESTINATION_PRINCIPALE. Ex. `{"COMM": "75056"}`,
                `{"DEP_CODE": "59", "AN_DEPOT": ("gte", 2024)}`,
                `{"COMM": ("in", ["75056", "69123"])}`.
            page: page 1-based. page_size ∈ {10, 20, 50, 100}.

        Returns:
            `{"total": int, "page": int, "page_size": int, "permis": [normalisés]}`.
        """
        if kind not in _PARSERS:
            raise ValueError(f"kind inconnu {kind!r} — attendu {KINDS}")
        if page_size not in DIDO_PAGE_SIZES:
            raise ValueError(f"page_size doit être dans {DIDO_PAGE_SIZES}, reçu {page_size}")
        parse = _PARSERS[kind]
        params: dict[str, Any] = {"page": page, "pageSize": page_size}
        for field, spec in (filters or {}).items():
            params[field] = _dido_filter(spec)
        r = self.session.get(f"{DIDO_BASE}/{rid}/rows", params=params, timeout=self.timeout)
        r.raise_for_status()
        body = r.json()
        rows = body.get("data") or []
        return {
            "total": body.get("total"),
            "page": body.get("page"),
            "page_size": page_size,
            "permis": [parse(_stringify(row)) for row in rows],
        }

    def search(
        self,
        kind: str = "locaux",
        *,
        communes: Optional[str | list[str]] = None,
        dept: Optional[str] = None,
        an_min: Optional[int] = None,
        an_max: Optional[int] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """`query()` de commodité : construit les filtres géo/temporels usuels.

        Args:
            kind: "logements" | "locaux" | "amenager".
            communes: code(s) INSEE commune (str ou liste → filtre `in`).
            dept: code département INSEE (ex. "59", "2A").
            an_min / an_max: bornes d'année de dépôt (incluses). Une plage fermée
                s'exprime via `in:` (DiDo n'accepte pas deux contraintes sur AN_DEPOT) ;
                une seule borne → `gte`/`lte`.
            page, page_size: pagination DiDo.
        """
        if kind not in RID_BY_KIND:
            raise ValueError(f"kind inconnu {kind!r} — attendu {tuple(RID_BY_KIND)}")
        filters: dict[str, Any] = {}
        if communes:
            comms = [communes] if isinstance(communes, str) else list(communes)
            filters["COMM"] = comms[0] if len(comms) == 1 else ("in", comms)
        if dept:
            filters["DEP_CODE"] = dept
        if an_min is not None and an_max is not None:
            filters["AN_DEPOT"] = ("in", list(range(an_min, an_max + 1)))
        elif an_min is not None:
            filters["AN_DEPOT"] = ("gte", an_min)
        elif an_max is not None:
            filters["AN_DEPOT"] = ("lte", an_max)
        return self.query(RID_BY_KIND[kind], kind, filters=filters, page=page, page_size=page_size)

    def iter_rows(self, csv_path: str | Path) -> Iterator[dict[str, str]]:
        """Lignes brutes du CSV (DictReader, `;`), en streaming."""
        with open(csv_path, encoding="utf-8", newline="") as f:
            yield from csv.DictReader(f, delimiter=";")

    def iter_permis(
        self,
        csv_path: str | Path,
        kind: str,
        depts: Optional[set[str]] = None,
        communes: Optional[set[str]] = None,
    ) -> Iterator[dict[str, Any]]:
        """Permis normalisés d'un fichier téléchargé, filtrés géographiquement.

        Args:
            csv_path: CSV téléchargé via `download()`.
            kind: "logements" | "locaux" | "amenager" (sélectionne le parseur).
            depts: codes département INSEE à garder (ex. {"75", "92"}). None = tous.
            communes: codes INSEE commune à garder. None = toutes.
        """
        if kind not in _PARSERS:
            raise ValueError(f"kind inconnu {kind!r} — attendu {KINDS}")
        parse = _PARSERS[kind]
        for row in self.iter_rows(csv_path):
            if depts is not None and row.get("DEP_CODE") not in depts:
                continue
            if communes is not None and row.get("COMM") not in communes:
                continue
            yield parse(row)
