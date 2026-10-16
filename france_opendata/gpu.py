"""Géoportail de l'Urbanisme (GPU) — zonage PLU/PLUi d'un point, via API Carto IGN.

Source : `https://apicarto.ign.fr/api/gpu/` (IGN, sans clé). Interroge les couches
du document d'urbanisme opposable (zonage, prescriptions, informations, servitudes,
document) sur un point, et assemble une **fiche zonage brute** :
- la zone primaire (PLU/PLUi DU_*) + les zones superposées (PSMV, SPR/AVAP, CC…),
- les prescriptions et informations (libellés bruts + code type + texte),
- les servitudes d'utilité publique,
- les documents couvrants,
- pour chaque zone/document : l'URL directe du PDF de règlement quand elle est
  reconstructible (partition + gpu_doc_id + nomfic).

Connecteur PUR : il livre la donnée GPU structurée, sans interprétation métier
(pas de heuristique mixité sociale / DPU / signaux — l'appelant interprète les
libellés). Géocodage et cadastre sont chez `BanClient` / `ApiCartoClient`.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from typing import Any, Optional

import requests

from .erreurs import ParametreInvalide

GPU_BASE = "https://apicarto.ign.fr/api/gpu/"
ANNEXES_BASE = "https://data.geopf.fr/annexes/gpu/documents"
# API du site Géoportail de l'Urbanisme : liste des pièces écrites d'un document publié
# (nom de fichier + intitulé normalisé CNIG, ex. « Règlement écrit »).
GPU_SITE_API = "https://www.geoportail-urbanisme.gouv.fr/api"
TIMEOUT = 30
RETRIES = 3  # apicarto/GPU renvoie des 503/timeout transitoires sous charge

# Couches GPU interrogées : (endpoint, catégorie logique).
GPU_LAYERS = [
    ("zone-urba", "zone"),
    ("secteur-cc", "secteur_cc"),
    ("prescription-surf", "prescription"),
    ("prescription-lin", "prescription"),
    ("prescription-pct", "prescription"),
    ("info-surf", "information"),
    ("info-lin", "information"),
    ("info-pct", "information"),
    ("document", "document"),
    ("assiette-sup-s", "servitude"),
]


# idurba (standard CNIG) : <territoire>_<type>_<date d'approbation>[_<code de secteur>]
# ex. 80021_PLU_20251211, 200046977_PLUI_20260326, 200054807_PLUi_20260306_B,
# 31555_PSMV_20250217. La partition GPU s'en déduit : DU_<territoire>[_<secteur>]
# pour un document d'urbanisme, PSMV_<territoire>[_<secteur>] pour un PSMV.
_IDURBA = re.compile(
    r"^(?P<territoire>[0-9A-Z]{5,9})_(?P<type>PLUI|PLU|POS|CC|PSMV)_(?P<date>\d{8})"
    r"(?:_(?P<secteur>[0-9A-Z]{1,5}))?$", re.IGNORECASE)

# La pièce « règlement écrit » se reconnaît à son intitulé GPU (« Règlement écrit ») OU à
# son nom de fichier CNIG, `<territoire>_reglement[_<n°|tome<n°>>]_<AAAAMMJJ>[_<secteur>].pdf`
# (le règlement graphique, `_reglement_graphique_…`, est le zonage ; une annexe nommée
# autrement n'est pas prise). L'intitulé est libre pour l'autorité qui publie : vu
# « L - Règlement - M - Règlement de zones spécifiques » pour 200054807_reglement_20260803_A.pdf
# (PLUi Aix-Marseille-Provence, volet A), qu'une recherche par intitulé seul manquait.
_TITRE_REGLEMENT_ECRIT = "reglement ecrit"
_NOM_PIECE = re.compile(r"^[\w.-]+\.pdf$", re.IGNORECASE)  # l'URL se construit avec
_NOM_REGLEMENT_ECRIT = re.compile(
    r"^(?P<territoire>[0-9A-Z]{5,9})_reglement(?:_(?:tome)?\d{1,2})?_\d{8}"
    r"(?:_[0-9A-Z]{1,5})?\.pdf$", re.IGNORECASE)


def _est_reglement_ecrit(piece: dict, territoire: str) -> bool:
    """La pièce est le règlement écrit du document : intitulé normalisé, ou nom CNIG du
    même territoire (un `PPRT_reglement` annexé n'en est pas un)."""
    nom = piece.get("name") or ""
    if not _NOM_PIECE.match(nom):
        return False
    if _sans_accents(piece.get("title") or "") == _TITRE_REGLEMENT_ECRIT:
        return True
    m = _NOM_REGLEMENT_ECRIT.match(nom)
    return bool(m) and m["territoire"].upper() == territoire.upper()


def _sans_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").casefold().strip()


class ReglementIntrouvable(LookupError):
    """Le GPU ne publie pas (ou plus) le règlement écrit de ce document.

    `autres_documents` = les idurba publiés en production sur la même partition : un
    document plus récent a pu remplacer celui demandé. Aucun n'est substitué en silence."""

    def __init__(self, message: str, *, autres_documents: Optional[list[str]] = None):
        self.autres_documents = autres_documents or []
        if self.autres_documents:
            message += " Documents publiés sur la partition : " + ", ".join(self.autres_documents) + "."
        super().__init__(message)


class GpuClient:
    """Client Géoportail de l'Urbanisme (API Carto IGN). Sans clé."""

    def __init__(self, timeout: int = TIMEOUT, retries: int = RETRIES):
        self._timeout = timeout
        self._retries = retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "france-opendata"})

    def _get(self, endpoint: str, params: dict) -> tuple[Optional[dict], Optional[str]]:
        last = None
        for attempt in range(self._retries):
            try:
                resp = self._session.get(GPU_BASE + endpoint, params=params, timeout=self._timeout)
                resp.raise_for_status()
                return resp.json(), None
            except Exception as e:  # noqa: BLE001 — on retente puis on dégrade par couche
                last = f"{type(e).__name__}: {e}"
                if attempt < self._retries - 1:
                    time.sleep(0.8 * (attempt + 1))
        return None, last

    def _get_json(self, url: str, params: Optional[dict] = None) -> Any:
        """GET JSON avec reprise sur erreur transitoire ; l'échec final PROPAGE (pas de
        dégradation : sans réponse on ne sait rien du document)."""
        for attempt in range(self._retries):
            last = attempt == self._retries - 1
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout):
                if last:
                    raise
            else:
                if resp.status_code < 500 or last:
                    resp.raise_for_status()
                    return resp.json()
            time.sleep(0.8 * (attempt + 1))
        raise RuntimeError("GpuClient._get_json : boucle de reprise épuisée")  # garde-fou

    @staticmethod
    def _idurba(idurba: str) -> re.Match:
        m = _IDURBA.match((idurba or "").strip())
        if not m:
            raise ParametreInvalide(
                "idurba", "format attendu <territoire>_<PLU|PLUi|POS|CC|PSMV>_<AAAAMMJJ>"
                          "[_<secteur>], tel que rendu par le zonage GPU")
        return m

    @staticmethod
    def partition_of(idurba: str) -> str:
        """Partition GPU d'un idurba (cf. `_IDURBA`). Refus d'entrée nommé si le format
        n'est pas celui du standard CNIG."""
        m = GpuClient._idurba(idurba)
        prefixe = "PSMV" if m["type"].upper() == "PSMV" else "DU"
        part = f"{prefixe}_{m['territoire'].upper()}"
        return f"{part}_{m['secteur'].upper()}" if m["secteur"] else part

    def documents(self, partition: str) -> list[dict]:
        """Documents publiés (couche `document` d'API Carto) d'une partition : propriétés
        seules (`name` = idurba, `gpu_doc_id`, `du_type`, `grid_title`, `gpu_status`…)."""
        data = self._get_json(GPU_BASE + "document", {"partition": partition})
        return [f.get("properties") or {} for f in (data or {}).get("features", [])]

    def pieces(self, gpu_doc_id: str) -> list[dict]:
        """Pièces écrites d'un document publié : `[{name, title, path}]`."""
        return self._get_json(f"{GPU_SITE_API}/document/{gpu_doc_id}/files") or []

    def reglement_ecrit(self, idurba: str) -> dict[str, Any]:
        """Résout le(s) PDF du règlement écrit d'un document d'urbanisme par son idurba.

        Le nom de fichier porté par la couche de zonage (`nomfic`) n'est pas fiable (vu :
        fichier absent du dépôt pendant le remplacement d'un PLUi) : on part du document
        publié (API Carto, couche `document`) et de sa liste de pièces (intitulé
        « Règlement écrit », ou nom de fichier CNIG du règlement écrit : l'intitulé est
        libre, cf. `_est_reglement_ecrit`). Un document peut garder les règlements de ses procédures
        antérieures (vu : six pièces de 2005 à 2019 pour un PLU de 2019) : seules les
        pièces datées du document sont retenues ; les autres sont nommées dans
        `pieces_ecartees`. Plusieurs pièces à la même date (tomes) sont toutes retenues.

        Returns:
            {idurba, partition, gpu_doc_id, du_type, titre, fichiers: [{nom, url}],
             pieces_ecartees, autres_documents} — `autres_documents` = les autres idurba
            publiés sur la partition (un remplaçant en cours de publication, à signaler
            sans substituer).
        Raises:
            ParametreInvalide: idurba mal formé.
            ReglementIntrouvable: document non publié sur la partition, ou sans pièce
                « règlement écrit » (une carte communale n'en a pas : le RNU s'applique).
        """
        partition = self.partition_of(idurba)
        m = self._idurba(idurba)
        date = m["date"]
        docs = self.documents(partition)
        doc = next((d for d in docs if (d.get("name") or "").upper() == idurba.strip().upper()), None)
        autres = [d["name"] for d in docs if d.get("name") and d is not doc]
        if doc is None:
            raise ReglementIntrouvable(
                f"Le document {idurba} n'est pas publié sur le Géoportail de l'Urbanisme "
                f"(partition {partition}).", autres_documents=autres)
        doc_id = doc.get("gpu_doc_id") or doc.get("id")
        pieces = self.pieces(doc_id)
        noms = [p["name"] for p in pieces if _est_reglement_ecrit(p, m["territoire"])]
        if not noms:
            raise ReglementIntrouvable(
                f"Le document {idurba} ne publie pas de règlement écrit sur le Géoportail "
                f"de l'Urbanisme ({len(pieces)} pièce(s) publiées).", autres_documents=autres)
        retenus = [n for n in noms if f"_{date}" in n] or (noms if len(noms) == 1 else [])
        if not retenus:
            raise ReglementIntrouvable(
                f"Le document {idurba} publie {len(noms)} pièces « règlement écrit », aucune "
                f"datée du document ({date}) : impossible de dire laquelle s'applique "
                f"({', '.join(sorted(noms))}).", autres_documents=autres)
        return {
            "idurba": idurba, "partition": partition, "gpu_doc_id": doc_id,
            "du_type": doc.get("du_type"), "titre": doc.get("grid_title"),
            "fichiers": [{"nom": n, "url": f"{ANNEXES_BASE}/{partition}/{doc_id}/{n}"}
                         for n in sorted(retenus)],
            "pieces_ecartees": sorted(set(noms) - set(retenus)),
            "autres_documents": autres,
        }

    @staticmethod
    def reglement_url(props: dict) -> Optional[str]:
        """URL directe du PDF de règlement (quelques Mo), constructible depuis les
        propriétés de la couche : partition + gpu_doc_id + nom de fichier. À NE PAS
        confondre avec le pack complet par partition (souvent plusieurs Go)."""
        part = props.get("partition")
        doc_id = props.get("gpu_doc_id")
        nomfic = props.get("nomfic")
        if part and doc_id and nomfic:
            return f"{ANNEXES_BASE}/{part}/{doc_id}/{nomfic}"
        return None

    @staticmethod
    def signaux(prescriptions: list[dict], informations: list[dict]) -> dict[str, Any]:
        """Dérive des flags d'urbanisme standards des libellés bruts GPU.

        Traduit prescriptions + informations (chaque item : `libelle`, opt. `texte`)
        en signaux pertinents pour l'analyse foncière : sursis à statuer, ZAD, DUP,
        emplacement réservé, EBC, mixité sociale, DPU, ZAC, OAP, périmètres commerce,
        risques. Les libellés bruts sont conservés à côté — ces règles par mots-clés
        sont heuristiques, l'appelant peut réinterpréter le texte source.
        """
        def _norm(s: Optional[str]) -> str:
            return (s or "").lower()

        sig: dict[str, Any] = {
            "mixite_sociale": [],      # quotas logement social (servitude L151-15 / SMS)
            "dpu": False,              # droit de préemption urbain
            "dpu_renforce": False,
            "zac": [],                 # zones d'aménagement concerté
            "oap": [],                 # orientations d'aménagement et de programmation
            "sursis_a_statuer": [],    # PLU en révision / permis différable — fort signal maîtrise foncière
            "zad": [],                 # zone d'aménagement différé — maîtrise/préemption longue
            "dup": [],                 # déclaration d'utilité publique — expropriation/projet public
            "emplacement_reserve": [], # ER — terrain grevé pour équipement public (souvent inconstructible privé)
            "espace_boise_classe": [], # EBC — inconstructible
            "perimetres_commerce": [], # protection / limitation commerce-artisanat
            "risques": [],             # aléas, PPR, bruit
            "autres": [],
        }
        for item in prescriptions + informations:
            lib = item["libelle"]
            n = _norm(lib)
            txt = item.get("texte") or ""
            tag = f"{lib} [{txt}]" if txt else lib
            if "mixite sociale" in n or "mixité sociale" in n or "logement social" in n:
                sig["mixite_sociale"].append(tag)
            elif "preemption" in n or "préemption" in n:
                sig["dpu"] = True
                if "renforce" in n or "renforcé" in n:
                    sig["dpu_renforce"] = True
            elif "sursis" in n and "statuer" in n:
                sig["sursis_a_statuer"].append(lib)
            elif "amenagement differe" in n or "aménagement différé" in n or "zad" in n.split():
                sig["zad"].append(lib)
            elif "emplacement reserve" in n or "emplacement réservé" in n or n.split()[:1] == ["er"]:
                sig["emplacement_reserve"].append(lib)
            elif "espace boise" in n or "espace boisé" in n or "ebc" in n.split():
                sig["espace_boise_classe"].append(lib)
            elif "utilite publique" in n or "utilité publique" in n:
                sig["dup"].append(lib)
            elif "zac" in n.split() or n.startswith("zac"):
                sig["zac"].append(lib)
            elif "oap" in n:
                sig["oap"].append(lib)
            elif any(k in n for k in ("commerce", "commercial", "artisanat")):
                sig["perimetres_commerce"].append(lib)
            elif any(k in n for k in ("alea", "aléa", "ppr", "risque", "argile", "inondation",
                                       "sonore", "retrait", "gonflement", "sismique")):
                sig["risques"].append(lib)
            else:
                sig["autres"].append(lib)
        return sig

    def query_point(self, lon: float, lat: float) -> tuple[dict[str, list], list[str]]:
        """Interroge toutes les couches GPU sur le point (EPSG:4326).

        Returns (features_par_catégorie, avertissements) — features bruts groupés
        par zone/secteur_cc/prescription/information/document/servitude.
        """
        geom = json.dumps({"type": "Point", "coordinates": [lon, lat]})
        out: dict[str, list] = {"zone": [], "secteur_cc": [], "prescription": [],
                                "information": [], "document": [], "servitude": []}
        warns: list[str] = []
        for endpoint, cat in GPU_LAYERS:
            data, err = self._get(endpoint, {"geom": geom})
            if err:
                warns.append(f"Couche {endpoint} : {err}")
                continue
            for feat in (data or {}).get("features", []):
                out[cat].append({"_endpoint": endpoint, "properties": feat.get("properties", {})})
        return out, warns

    def zonage(self, lon: float, lat: float) -> dict[str, Any]:
        """Fiche zonage brute du document d'urbanisme opposable au point (lon, lat).

        Args:
            lon, lat: coordonnées EPSG:4326 (géocoder l'adresse au préalable).

        Returns:
            dict {zone, zones_superposees, prescriptions, informations,
            servitudes_utilite_publique, document, documents_couvrants,
            avertissements, sources}. `zone`/`document` à null si aucun document
            numérisé (commune au RNU ou PLU non publié sur le GPU).
        """
        gpu, warns = self.query_point(lon, lat)

        prescriptions = [{
            "categorie": it["_endpoint"].replace("prescription-", ""),
            "libelle": it["properties"].get("libelle"),
            "type_code": it["properties"].get("typepsc"),
            "texte": it["properties"].get("txt") or "",
        } for it in gpu["prescription"]]

        informations = [{
            "categorie": it["_endpoint"].replace("info-", ""),
            "libelle": it["properties"].get("libelle"),
            "type_code": it["properties"].get("typeinf"),
        } for it in gpu["information"]]

        zones_props = [z["properties"] for z in gpu["zone"]]

        def _is_plu(p: dict) -> bool:
            return (p.get("partition") or "").startswith("DU_")

        zone = None
        superpositions: list[dict] = []
        if zones_props:
            primary = next((p for p in zones_props if _is_plu(p)), zones_props[0])
            zone = {
                "libelle": primary.get("libelle"),
                "libelle_long": primary.get("libelong"),
                "type_zone": primary.get("typezone"),
                "destination_dominante": primary.get("destdomi"),
                "date_validation": primary.get("datvalid"),
                "idurba": primary.get("idurba"),
                "partition": primary.get("partition"),
                "reglement_url": self.reglement_url(primary),
            }
            for p in zones_props:
                if p is primary:
                    continue
                superpositions.append({
                    "libelle": p.get("libelle"),
                    "partition": p.get("partition"),
                    "idurba": p.get("idurba"),
                    "reglement_url": self.reglement_url(p),
                })
        elif gpu["secteur_cc"]:
            sp = gpu["secteur_cc"][0]["properties"]
            zone = {
                "libelle": sp.get("libelle"),
                "type_zone": "secteur de carte communale",
                "idurba": sp.get("idurba"),
                "partition": sp.get("partition"),
                "reglement_url": self.reglement_url(sp),
            }

        documents = [{
            "type": it["properties"].get("du_type"),
            "nom": it["properties"].get("name") or it["properties"].get("grid_title"),
            "intitule": it["properties"].get("grid_title"),
            "partition": it["properties"].get("partition"),
            "gpu_status": it["properties"].get("gpu_status"),
        } for it in gpu["document"]]
        document = next((d for d in documents if (d.get("partition") or "").startswith("DU_")),
                        documents[0] if documents else None)

        servitudes = [it["properties"].get("libelle") for it in gpu["servitude"]
                      if it["properties"].get("libelle")]

        if not document and not zone:
            warns.append("Aucun document d'urbanisme numérisé trouvé sur ce point "
                         "(commune au RNU, ou PLU non publié sur le GPU).")

        return {
            "zone": zone,
            "zones_superposees": superpositions,
            "prescriptions": prescriptions,
            "informations": informations,
            "servitudes_utilite_publique": servitudes,
            "document_urbanisme": document,
            "documents_couvrants": documents,
            "avertissements": warns,
            "sources": {
                "urbanisme": "Géoportail de l'Urbanisme via API Carto IGN (apicarto.ign.fr/api/gpu)",
            },
        }
