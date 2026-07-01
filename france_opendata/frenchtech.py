"""French Tech (capitales régionales) — annuaire écosystème + événements + financements.

Les capitales French Tech publient leur site sur un **WordPress** dont l'API REST
native (`/wp-json/wp/v2/`) expose en JSON, sans clé, des *custom post types* :

- `annuaire`             : entreprises de l'écosystème (startups, structures
  d'accompagnement, prestataires) — champs ACF riches : pitch, adresse, ville,
  **dirigeant, email, téléphone, site web**, effectif, CA, besoins.
- `membre`               : personnes membres (fiches individuelles).
- `agenda`               : événements (meetups, conférences).
- `appel_candidatures`   : appels à projet / concours / AMI.
- `dispositif_financeme` : dispositifs de financement (avec type, montant, critères).

Taxonomies filtrables via leurs propres endpoints REST (`secteur_activite`,
`type_annuaire`, `localisation`, `type_financement`, `stade`, …).

Le connecteur **tape l'API en direct** (données vivantes, pas de pré-fetch) : c'est
la config WordPress par défaut. Si un site ferme son REST ou change de structure,
le client **lève une erreur** (pas de fallback scraping caché).

Bonus — **French Tech Central** : la prise de RDV avec les correspondants de l'État
est déléguée à Synbird. `ftc_scenarios()` récupère en direct (POST public, sans
token) la liste des prestations bookables d'une page French Tech Central.

Multi-capitales : `base_url` est paramétrable (défaut Aix-Marseille). Les autres
capitales ont chacune leur WordPress + éventuellement leur page Synbird.

    from france_opendata import FrenchTechClient
    ft = FrenchTechClient()  # aix-marseille par défaut
    ft.search_annuaire(query="IA", ville="Marseille")
    ft.list_evenements(per_page=10)
    ft.ftc_scenarios()
"""
from __future__ import annotations

import html
import re
from typing import Any, Iterable, Optional

import requests

from ._http import DEFAULT_TIMEOUT

DEFAULT_BASE_URL = "https://lafrenchtech-aixmarseille.fr"
DEFAULT_FTC_COMPANY_ID = "649"  # id_professional_company Synbird de la page FTC Aix-Marseille
SYNBIRD_WIDGET_URL = "https://ws.synbird.com/v6/public/company/getWidget"

# _fields demandés à l'API WP : garde la réponse propre (sinon ~50 clés _oembed_* de bruit).
_WP_FIELDS = (
    "id,slug,link,date,modified,title,content,excerpt,acf,"
    "type_annuaire,secteur_activite,categorie_structure,localisation,"
    "type_financement,montant_maximum,critere_elegibilite,stade"
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _text(html_str: Optional[str]) -> str:
    """HTML rendu → texte plat (l'API renvoie du HTML dans content/excerpt)."""
    if not html_str:
        return ""
    return html.unescape(_WS_RE.sub(" ", _TAG_RE.sub(" ", html_str)).strip())


def _first(*vals: Any) -> Any:
    for v in vals:
        if v not in (None, "", False, [], {}):
            return v
    return None


class FrenchTechClient:
    """Client REST d'un site de capitale French Tech (WordPress) + French Tech Central."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        ftc_company_id: str = DEFAULT_FTC_COMPANY_ID,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.wp = f"{self.base_url}/wp-json/wp/v2"
        self.ftc_company_id = ftc_company_id
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "france-opendata/frenchtech"})
        self._term_cache: dict[str, dict[int, str]] = {}

    # ------------------------------------------------------------------ HTTP WP

    def _wp_get(self, endpoint: str, **params: Any) -> tuple[list[dict], int]:
        """Un GET REST WP. Retourne (records, total_pages)."""
        r = self._session.get(f"{self.wp}/{endpoint}", params=params, timeout=self.timeout)
        r.raise_for_status()
        total_pages = int(r.headers.get("X-WP-TotalPages", 1) or 1)
        return r.json(), total_pages

    def _wp_collect(
        self,
        endpoint: str,
        *,
        per_page: int = 100,
        max_pages: Optional[int] = None,
        **params: Any,
    ) -> Iterable[dict]:
        """Itère toutes les pages d'un post type (per_page plafonné à 100 par WP)."""
        params.setdefault("_fields", _WP_FIELDS)
        page = 1
        while True:
            records, total_pages = self._wp_get(
                endpoint, per_page=min(per_page, 100), page=page, **params
            )
            yield from records
            if page >= total_pages or (max_pages and page >= max_pages):
                break
            page += 1

    # -------------------------------------------------------------- taxonomies

    def taxonomy_terms(self, taxonomy: str) -> dict[int, str]:
        """{term_id: nom} d'une taxonomie (ex. 'secteur_activite'), mis en cache."""
        if taxonomy not in self._term_cache:
            terms: dict[int, str] = {}
            for t in self._wp_collect(taxonomy, _fields="id,name,slug,count", per_page=100):
                terms[t["id"]] = t.get("name", "")
            self._term_cache[taxonomy] = terms
        return self._term_cache[taxonomy]

    def _resolve(self, taxonomy: str, ids: Any) -> list[str]:
        if not ids:
            return []
        table = self.taxonomy_terms(taxonomy)
        return [table[i] for i in ids if i in table]

    def _term_id(self, taxonomy: str, name: str) -> Optional[int]:
        """Résout un nom de terme (insensible à la casse) → id, pour filtrer."""
        low = name.strip().lower()
        for tid, tname in self.taxonomy_terms(taxonomy).items():
            if tname.strip().lower() == low:
                return tid
        return None

    # ----------------------------------------------------------- normalisation

    def _norm_annuaire(self, e: dict) -> dict:
        acf = e.get("acf") or {}
        cp = (acf.get("code_postal") or "").strip()
        return {
            "slug": e.get("slug"),
            "nom": _text(e.get("title", {}).get("rendered")),
            "type": self._resolve("type_annuaire", e.get("type_annuaire")),
            "secteurs": self._resolve("secteur_activite", e.get("secteur_activite")),
            "pitch": _text(acf.get("bloc_pitch")),
            "dirigeant": _first(acf.get("nom_dirigeant")),
            "email": _first(acf.get("email_boss_")),
            "telephone": _first(acf.get("telephone")),
            "website": _first(acf.get("website_")),
            "ville": _first(acf.get("ville")),
            "code_postal": cp or None,
            "adresse": _first(acf.get("adresse_1"), acf.get("adresse_2")),
            "date_creation": _first(acf.get("datecreation")),
            "effectif": _first(acf.get("nbsalaries_")),
            "ca": _first(acf.get("ca2019_")),
            "fonds_leves": _first(acf.get("fondsleves_")),
            "recrute": _first(acf.get("recrut2020_")),
            "recherche_fonds": _first(acf.get("rechfonds_")),
            "besoins": _text(acf.get("besoins_")) or None,
            "link": e.get("link"),
        }

    def _norm_generic(self, e: dict, *, taxonomies: tuple[str, ...] = ()) -> dict:
        out = {
            "slug": e.get("slug"),
            "titre": _text(e.get("title", {}).get("rendered")),
            "date": e.get("date"),
            "resume": _text(e.get("excerpt", {}).get("rendered"))
            or _text(e.get("content", {}).get("rendered"))[:400],
            "link": e.get("link"),
        }
        for tax in taxonomies:
            out[tax] = self._resolve(tax, e.get(tax))
        return out

    # -------------------------------------------------------------- annuaire

    def search_annuaire(
        self,
        *,
        query: Optional[str] = None,
        secteur: Optional[str] = None,
        ville: Optional[str] = None,
        type_annuaire: Optional[str] = None,
        all_results: bool = False,
        per_page: int = 100,
        max_pages: Optional[int] = None,
    ) -> dict[str, Any]:
        """Entreprises de l'annuaire (startups / structures / prestataires).

        `query` : recherche plein-texte. `secteur` / `type_annuaire` : nom de terme
        de taxonomie (résolu → id). `ville` : filtre sur le champ ACF ville (côté
        client, l'ACF n'étant pas indexé côté WP). `all_results=True` pagine tout
        (~694 fiches). Retourne `{"total": int, "results": [ {nom, dirigeant, email,
        telephone, website, secteurs, ville, …}, … ]}`.
        """
        params: dict[str, Any] = {}
        if query:
            params["search"] = query
        if secteur:
            tid = self._term_id("secteur_activite", secteur)
            if tid is None:
                raise ValueError(f"secteur inconnu: {secteur!r}")
            params["secteur_activite"] = tid
        if type_annuaire:
            tid = self._term_id("type_annuaire", type_annuaire)
            if tid is None:
                raise ValueError(f"type_annuaire inconnu: {type_annuaire!r}")
            params["type_annuaire"] = tid

        pages = None if all_results else 1
        pages = max_pages if max_pages is not None else pages
        results = [self._norm_annuaire(e) for e in
                   self._wp_collect("annuaire", per_page=per_page, max_pages=pages, **params)]
        if ville:
            low = ville.strip().lower()
            results = [r for r in results if (r["ville"] or "").strip().lower() == low]
        return {"total": len(results), "results": results}

    def get_annuaire(self, slug: str) -> Optional[dict]:
        """Fiche entreprise par slug."""
        records, _ = self._wp_get("annuaire", slug=slug, _fields=_WP_FIELDS)
        return self._norm_annuaire(records[0]) if records else None

    # ------------------------------------------------- membres / events / etc.

    def list_membres(self, *, query: Optional[str] = None, all_results: bool = False,
                     per_page: int = 100) -> dict[str, Any]:
        """Personnes membres de la communauté."""
        params = {"search": query} if query else {}
        pages = None if all_results else 1
        results = [self._norm_generic(e, taxonomies=("localisation",))
                   for e in self._wp_collect("membre", per_page=per_page, max_pages=pages, **params)]
        return {"total": len(results), "results": results}

    def list_evenements(self, *, query: Optional[str] = None, all_results: bool = False,
                        per_page: int = 50) -> dict[str, Any]:
        """Événements de l'agenda (meetups, confs). Triés API = plus récents d'abord."""
        params = {"search": query} if query else {}
        pages = None if all_results else 1
        results = [self._norm_generic(e, taxonomies=("localisation",))
                   for e in self._wp_collect("agenda", per_page=per_page, max_pages=pages, **params)]
        return {"total": len(results), "results": results}

    def list_appels(self, *, query: Optional[str] = None, all_results: bool = False,
                    per_page: int = 50) -> dict[str, Any]:
        """Appels à projet / concours / AMI (`appel_candidatures`)."""
        params = {"search": query} if query else {}
        pages = None if all_results else 1
        results = [self._norm_generic(e, taxonomies=("localisation",))
                   for e in self._wp_collect("appel_candidatures", per_page=per_page, max_pages=pages, **params)]
        return {"total": len(results), "results": results}

    def list_financements(self, *, query: Optional[str] = None, all_results: bool = False,
                          per_page: int = 100) -> dict[str, Any]:
        """Dispositifs de financement (avec type, montant, stade, critères)."""
        params = {"search": query} if query else {}
        pages = None if all_results else 1
        taxos = ("type_financement", "montant_maximum", "critere_elegibilite", "stade", "localisation")
        results = [self._norm_generic(e, taxonomies=taxos)
                   for e in self._wp_collect("dispositif_financeme", per_page=per_page, max_pages=pages, **params)]
        return {"total": len(results), "results": results}

    # ------------------------------------------------- French Tech Central (Synbird)

    def ftc_scenarios(self, company_id: Optional[str] = None) -> dict[str, Any]:
        """Prestations bookables French Tech Central (RDV correspondants de l'État).

        POST public Synbird (sans token). Retourne `{"company": str, "scenarios":
        [ {name, duration, price}, … ]}`. Le questionnaire d'aiguillage (quel
        organisme pour quel sous-besoin) est un arbre imbriqué non reconstruit ici :
        on expose la liste plate des prestations racines.
        """
        cid = company_id or self.ftc_company_id
        r = self._session.post(
            SYNBIRD_WIDGET_URL,
            json={"id_professional_company": str(cid)},
            timeout=self.timeout,
        )
        r.raise_for_status()
        payload = r.json()
        companies = payload.get("companies") or []
        if not companies:
            raise RuntimeError(f"Synbird: aucune company pour id={cid}")
        company = companies[0]
        scenarios: list[dict] = []
        for activity in company.get("activities") or []:
            for p in activity.get("prestations") or []:
                scenarios.append({
                    "name": (p.get("name") or "").strip(),
                    "duration": p.get("duration"),
                    "price": p.get("price"),
                })
        return {"company": (company.get("corporate_name") or "").strip(), "scenarios": scenarios}
