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
import time
from typing import Any, Optional

import requests

GPU_BASE = "https://apicarto.ign.fr/api/gpu/"
ANNEXES_BASE = "https://data.geopf.fr/annexes/gpu/documents"
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
