"""Aides publiques aux entreprises — base data.aides-entreprises.fr (ISM, réf. État).

Dumps ouverts sans clé (licence ouverte, republiés quotidiennement) :
`aides.json` (~54 Mo, ~2 400 aides actives) + `territoires.json` (~12 Mo,
hiérarchie commune → EPCI/département → région → France via le champ `parents`).
Chaque aide est auto-suffisante (`cache_indexation` embarque territoires, natures,
financeurs, contacts) — aucune jointure au moment de la requête.

Le client télécharge les dumps au premier appel et les garde en mémoire (TTL 24 h,
rythme de republication de la base). `search()` applique le **filtre déterministe**
géo + taille + nature + échéance + lexical ; la base élague elle-même les aides
périmées.

⚠️ Pertinence sectorielle : le tagging structurel de la base ne discrimine PAS
(« PME tous secteurs » est posé sur ~99 % des aides). Le tri fin doit sortir du
TEXTE (`aid_objet`, `aid_conditions`, `aid_benef`) — ce client fournit la shortlist
déterministe, le re-rank sémantique appartient à l'appelant. Pour éviter les
hallucinations du re-rank : ne faire produire que des `id_aid`, puis re-rendre les
fiches depuis `get()` — jamais reprendre nom/objet de la sortie du modèle.
"""
from __future__ import annotations

import html
import re
import time
from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

BASE_URL = "https://data.aides-entreprises.fr/files"
DEFAULT_TTL = 24 * 3600  # les dumps sont republiés quotidiennement

# Codes de tranche d'effectif de la base (référentiel du moteur officiel
# aides-entreprises.fr ; le code 6 n'est pas exposé par le site, observé en
# extension du 5 sur les dispositifs grandes entreprises).
# 1=micro-entreprise, 2=<10 salariés, 3=10-49, 4=50-249, 5=250+, 6=au-delà.
_NIVEAU = {"1": "territoriale", "2": "nationale", "3": "européenne"}

_TAG_RE = re.compile(r"<[^>]+>")

# Paris/Lyon/Marseille : le référentiel territoires n'indexe QUE les
# arrondissements (751xx/693xx/132xx) — le code COG de la commune-mère y est
# absent. On le résout en l'union de ses arrondissements (l'entité « ville »
# du référentiel, sans insee, est un ancêtre commun → couverte via `parents`).
_PLM_ARRONDISSEMENTS = {"75056": "751", "69123": "693", "13055": "132"}


def _text(raw: Optional[str]) -> str:
    """HTML embarqué (entités + balises) → texte nu normalisé."""
    if not raw:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html.unescape(raw))).strip()


# Champs top-level porteurs de HTML embarqué (entités + balises) — à décoder comme
# compact() le fait pour la recherche.
_TEXT_FIELDS = (
    "aid_nom", "aid_objet", "aid_montant", "aid_conditions",
    "aid_benef", "aid_operations_el", "aid_validation",
)


def _links(items) -> list[dict]:
    """`complements.*` → liens actionnables nus (texte décodé + lien + date)."""
    return [{"texte": _text(s.get("texte")), "lien": s.get("lien"), "date": s.get("date")}
            for s in (items or []) if isinstance(s, dict) and s.get("lien")]


def _codes_for_effectif(n: int) -> set[str]:
    if n <= 0:
        return {"1", "2"}
    if n < 10:
        return {"2"}
    if n < 50:
        return {"3"}
    if n < 250:
        return {"4"}
    return {"5", "6"}


def _date_fin(a: dict) -> Optional[str]:
    """`date_fin` réelle (YYYY-MM-DD) ou None (la base met '0000-00-00 00:00:00')."""
    raw = str(a.get("date_fin") or "")
    return raw[:10] if raw[:4].isdigit() and raw[:4] != "0000" else None


class AidesClient:
    """Client des aides publiques aux entreprises (subventions, prêts, AAP…)."""

    def __init__(self, ttl: int = DEFAULT_TTL):
        self.ttl = ttl
        self._loaded_at: float = 0.0
        self._aides: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._ter_by_insee: dict[str, dict] = {}
        self._ter_by_cp: dict[str, list[dict]] = {}

    # --- chargement -----------------------------------------------------------

    def _fetch(self, name: str) -> list[dict]:
        r = requests.get(f"{BASE_URL}/{name}", timeout=(DEFAULT_TIMEOUT[0], 120))
        r.raise_for_status()
        payload = r.json()
        return payload["data"] if isinstance(payload, dict) else payload

    def _ensure_loaded(self) -> None:
        if self._aides and (time.time() - self._loaded_at) < self.ttl:
            return
        aides = [a for a in self._fetch("aides.json") if str(a.get("status")) == "1"]
        terrs = self._fetch("territoires.json")
        by_insee: dict[str, dict] = {}
        by_cp: dict[str, list[dict]] = {}
        for t in terrs:
            if str(t.get("DEL") or "0") == "1" or str(t.get("status") or "1") != "1":
                continue
            if t.get("insee"):
                by_insee[t["insee"]] = t
                # ter_code d'une commune = code postal principal
                if t.get("ter_code"):
                    by_cp.setdefault(t["ter_code"], []).append(t)
        # publication atomique (le client peut être partagé entre threads)
        self._by_id = {str(a["id_aid"]): a for a in aides}
        self._aides = aides
        self._ter_by_insee = by_insee
        self._ter_by_cp = by_cp
        self._loaded_at = time.time()

    # --- lecture --------------------------------------------------------------

    def _eligible_ter_ids(self, insee: Optional[str],
                          code_postal: Optional[str]) -> Optional[set[str]]:
        """{id_ter commune} ∪ ancêtres, ou None si pas de filtre géo.

        Lève ValueError si la commune est introuvable (pas de résultat silencieux
        faussement vide)."""
        if not insee and not code_postal:
            return None
        ters: list[dict] = []
        if insee:
            prefix = _PLM_ARRONDISSEMENTS.get(str(insee))
            if prefix:
                ters = [t for i, t in self._ter_by_insee.items() if i.startswith(prefix)]
            else:
                t = self._ter_by_insee.get(str(insee))
                ters = [t] if t else []
            if not ters:
                raise ValueError(f"Commune INSEE {insee} inconnue du référentiel territoires")
        else:
            ters = self._ter_by_cp.get(str(code_postal), [])
            if not ters:
                raise ValueError(
                    f"Code postal {code_postal} inconnu du référentiel territoires "
                    "(préférer le code INSEE de la commune)")
        eligible: set[str] = set()
        for t in ters:
            eligible.add(t["id_ter"])
            eligible.update(p for p in (t.get("parents") or "").split(",") if p)
        return eligible

    def compact(self, a: dict, max_chars: int = 400) -> dict:
        """Fiche courte scannable (la fiche complète = `get`)."""
        ci = a.get("cache_indexation") or {}
        comp = a.get("complements")
        raw_sources = comp.get("source", []) if isinstance(comp, dict) else []
        sources = [s.get("lien") for s in raw_sources
                   if isinstance(s, dict) and s.get("lien")]
        return {
            "id": str(a["id_aid"]),
            "nom": _text(a.get("aid_nom")),
            "objet": _text(a.get("aid_objet"))[:max_chars],
            "montant": _text(a.get("aid_montant"))[:max_chars],
            "natures": [n.get("typ_libelle") for n in ci.get("natures", [])],
            "financeurs": [f.get("org_nom") for f in ci.get("financeurs", [])],
            "niveau": _NIVEAU.get(str(a.get("couverture_geo")), None),
            "date_fin": _date_fin(a),
            "effectif_codes": str(a.get("effectif") or ""),
            "source_url": sources[0] if sources else None,
        }

    def search(
        self,
        insee: Optional[str] = None,
        code_postal: Optional[str] = None,
        effectif: Optional[int] = None,
        nature: Optional[str] = None,
        echeance_avant: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Shortlist déterministe d'aides. Renvoie l'entonnoir mesuré + les fiches.

        - géo : une aide s'applique si un de ses territoires ∈ {commune ∪ ancêtres}
          (les aides nationales/européennes pointent FRANCE → toujours couvertes) ;
        - effectif : garde les aides dont les codes de tranche couvrent `effectif`
          (champ vide = pas de restriction) ;
        - nature : sous-chaîne du libellé (Subvention, Prêt, Garantie…) ;
        - echeance_avant (YYYY-MM-DD) : aides À échéance, qui clôturent avant la
          date (veille AAP) ;
        - q : filtre lexical AND sur nom+objet+conditions+bénéficiaires — un
          pré-filtre grossier, PAS un tri de pertinence.
        """
        self._ensure_loaded()
        funnel: dict[str, int] = {"base": len(self._aides)}
        rows = self._aides

        eligible = self._eligible_ter_ids(insee, code_postal)
        if eligible is not None:
            rows = [a for a in rows
                    if {t["id_ter"] for t in (a.get("cache_indexation") or {})
                        .get("territoires", [])} & eligible]
            funnel["geo"] = len(rows)

        if effectif is not None:
            codes = _codes_for_effectif(int(effectif))
            rows = [a for a in rows
                    if not str(a.get("effectif") or "").strip()
                    or set(str(a["effectif"]).split(",")) & codes]
            funnel["effectif"] = len(rows)

        if nature:
            needle = nature.strip().lower()
            rows = [a for a in rows
                    if any(needle in (n.get("typ_libelle") or "").lower()
                           for n in (a.get("cache_indexation") or {}).get("natures", []))]
            funnel["nature"] = len(rows)

        if echeance_avant:
            rows = [a for a in rows
                    if (d := _date_fin(a)) and d <= str(echeance_avant)]
            funnel["echeance"] = len(rows)

        if q:
            tokens = [t for t in q.lower().split() if len(t) > 2]
            def _haystack(a: dict) -> str:
                return _text(" ".join(str(a.get(k) or "") for k in
                                      ("aid_nom", "aid_objet", "aid_conditions",
                                       "aid_benef"))).lower()
            rows = [a for a in rows if (h := _haystack(a)) and all(t in h for t in tokens)]
            funnel["texte"] = len(rows)

        return {
            "funnel": funnel,
            "count": len(rows),
            "items": [self.compact(a) for a in rows[offset:offset + limit]],
        }

    def detail(self, a: dict) -> dict:
        """Fiche complète NETTOYÉE : champs texte décodés (`_text`) + extraits utiles
        de `cache_indexation`/`complements`, débarrassés du bruit de jointure (id_file,
        count, status, miseajour, echelon, lat/lng, logo…). Le brut intégral reste
        accessible via `get(id, raw=True)`."""
        ci = a.get("cache_indexation") or {}
        comp = a.get("complements") if isinstance(a.get("complements"), dict) else {}
        out = {k: v for k, v in a.items()
               if k not in ("cache_indexation", "complements")}
        for f in _TEXT_FIELDS:
            if f in out:
                out[f] = _text(out.get(f))
        out["date_fin"] = _date_fin(a)
        out["niveau"] = _NIVEAU.get(str(a.get("couverture_geo")), None)
        out["natures"] = [n.get("typ_libelle") for n in ci.get("natures", [])]
        out["financeurs"] = [
            {k2: f.get(k2) for k2 in
             ("org_nom", "org_ville", "org_telephone", "org_email", "org_site")
             if f.get(k2)}
            for f in ci.get("financeurs", [])]
        out["territoires"] = [
            {"insee": t.get("insee") or None, "libelle": t.get("ter_libelle"),
             "code": t.get("ter_code")}
            for t in ci.get("territoires", [])]
        out["contacts"] = [
            {k2: c.get(k2) for k2 in
             ("cnt_nom", "cnt_ville", "cnt_telephone", "email", "cnt_site")
             if c.get(k2)}
            for c in ci.get("contacts", [])]
        out["sources"] = _links(comp.get("source"))
        out["formulaires"] = _links(comp.get("formulaire"))
        return out

    def get(self, id_aid: str | int, raw: bool = False) -> Optional[dict]:
        """Fiche complète d'une aide (source de vérité post-re-rank).

        Par défaut NETTOYÉE (`detail` : texte décodé + extraits de `cache_indexation`) ;
        `raw=True` rend l'enregistrement brut de la base."""
        self._ensure_loaded()
        a = self._by_id.get(str(id_aid))
        if a is None:
            return None
        return a if raw else self.detail(a)
