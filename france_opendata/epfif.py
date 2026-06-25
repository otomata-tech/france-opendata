"""EPFIF — secteurs d'intervention de l'Établissement Public Foncier d'Île-de-France.

L'EPFIF intervient sur ~350 communes franciliennes via des **veilles**, **maîtrises
foncières** et **ORCOD-IN** : un signal « maîtrise foncière » que le zonage GPU ne
porte pas (commune sous intervention = foncier sous tension, préemption souvent
déléguée à l'EPFIF).

⚠️ Pas d'open data téléchargeable côté EPFIF : la donnée vit dans le menu HTML de la
page cartographie (`epfif.fr/cartographie/`), commune → interventions typées. Ce client
**fetch la page en direct, la parse, et met le résultat en cache** (TTL long — la donnée
bouge à l'échelle des conventions foncières). Le scrape dépend de la structure HTML :
en cas d'échec/structure changée, le client **dégrade** (sert le dernier cache valide,
ou renvoie un statut indisponible) plutôt que d'inventer.

Couverture régionale (Île-de-France uniquement) : hors IDF, `secteur_epfif`=False.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

import requests

CARTO_URL = "https://www.epfif.fr/cartographie/"
TIMEOUT = 60
DEFAULT_TTL = 7 * 24 * 3600  # la donnée bouge lentement → refetch hebdomadaire
MIN_COMMUNES = 100  # en-dessous : structure de page probablement cassée → ne pas publier

# data-type de l'EPFIF → type métier normalisé, et force du signal foncier.
_TYPE = {"veilles": "veille", "maitrises": "maitrise", "orcodins": "orcodin"}
_FORCE = {"orcodin": 3, "maitrise": 2, "veille": 1}
_MINUSCULES = {"sur", "sous", "en", "le", "la", "les", "du", "de", "des", "lès",
               "et", "d", "l", "aux", "au"}

_RE_COMMUNE = re.compile(r'data-name="([^"]+)"\s+data-submenu="submenu-(\d+)-(\w{5})"')
_RE_BLOCK = re.compile(r'<ul data-menu="submenu-(\d+)-(\w{5})"[^>]*>(.*?)</ul>', re.DOTALL)
_RE_OP = re.compile(
    r'<a class="menu__link link-commune"[^>]*data-type="(\w+)"[^>]*'
    r'data-kmlid="(\d+)"[^>]*>([^<]+)</a>'
)


def _clean_name(s: Optional[str]) -> Optional[str]:
    """La source met les lettres accentuées en MAJUSCULE (« chÂtillon ») : on
    reconstruit une casse FR correcte (petits mots de liaison en minuscule)."""
    if not s:
        return s
    out = []
    for tok in re.split(r"([\s/-])", s.lower()):
        if tok in (" ", "-", "/", "") or tok in _MINUSCULES:
            out.append(tok)
        else:
            out.append(tok[:1].upper() + tok[1:])
    return "".join(out)


def _parse_op_text(txt: str) -> tuple[Optional[str], Optional[str]]:
    """'Commune / partenaire - 17/02/2017' → (partenaire, date)."""
    txt = txt.strip()
    date = None
    if " - " in txt:
        head, date = txt.rsplit(" - ", 1)
        date = date.strip()
    else:
        head = txt
    partenaire = head.split(" / ", 1)[1].strip() if " / " in head else None
    return partenaire, date


def parse(html: str) -> dict[str, Any]:
    """Parse le menu HTML de la page cartographie EPFIF → index par code INSEE."""
    noms = {insee: _clean_name(nom) for nom, _dep, insee in _RE_COMMUNE.findall(html)}
    communes: dict[str, dict] = {}
    nb_interventions = 0
    for m in _RE_BLOCK.finditer(html):
        dep, insee, content = m.groups()
        ops = []
        for raw_type, kmlid, txt in _RE_OP.findall(content):
            partenaire, date = _parse_op_text(txt)
            ops.append({"type": _TYPE.get(raw_type, raw_type),
                        "partenaire": _clean_name(partenaire), "date": date, "kmlid": kmlid})
        if not ops:
            continue
        nb_interventions += len(ops)
        communes[insee] = {"nom": noms.get(insee), "departement": dep, "interventions": ops}
    return {"source": CARTO_URL, "nb_communes": len(communes),
            "nb_interventions": nb_interventions, "communes": dict(sorted(communes.items()))}


class EpfifClient:
    """Client EPFIF — sans clé, Île-de-France uniquement. Deux modes :

    - **index injecté** (`EpfifClient(index=<dict>)`) : sert un index statique pré-fetché
      (sortie de `parse`), **ne scrape JAMAIS** — instantané, offline, version-pinné.
      Mode recommandé en production (cf. OGIC : index committé rafraîchi au build).
    - **live** (défaut) : le premier appel fetch+parse la page cartographie, cache TTL ;
      dégrade sur le dernier cache valide si la source casse.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL, timeout: int = TIMEOUT,
                 index: Optional[dict] = None):
        self._ttl = ttl_seconds
        self._timeout = timeout
        self._static = index  # si fourni : index statique, aucun accès réseau
        self._session = None if index is not None else requests.Session()
        if self._session is not None:
            self._session.headers.update({"User-Agent": "france-opendata/epfif"})
        self._cache: Optional[dict] = index
        self._cached_at: float = 0.0
        self._stale: bool = False  # dernier refresh dégradé (fetch/parse KO)

    def _fetch(self) -> str:
        resp = self._session.get(CARTO_URL, timeout=self._timeout)
        resp.raise_for_status()
        return resp.text

    def _index(self) -> dict:
        """Index courant. En mode statique : l'index injecté, sans jamais fetch.
        En mode live : rafraîchi si périmé, dégrade sur le dernier cache valide."""
        if self._static is not None:
            return self._static
        fresh = self._cache is not None and (time.monotonic() - self._cached_at) < self._ttl
        if fresh:
            return self._cache
        try:
            idx = parse(self._fetch())
            if idx["nb_communes"] < MIN_COMMUNES:
                raise ValueError(f"{idx['nb_communes']} communes parsées (<{MIN_COMMUNES}) "
                                 f"— structure de page probablement changée")
            self._cache, self._cached_at, self._stale = idx, time.monotonic(), False
            return idx
        except Exception:  # noqa: BLE001 — dégradation : on ne casse pas l'appelant
            self._stale = True
            if self._cache is not None:
                return self._cache
            raise

    def lookup(self, code_insee: str) -> dict[str, Any]:
        """Statut EPFIF d'une commune. `secteur_epfif`=False si hors secteur connu,
        None si la source est indisponible (et aucun cache)."""
        try:
            idx = self._index()
        except Exception as e:  # noqa: BLE001
            return {"secteur_epfif": None,
                    "note": f"Source EPFIF indisponible ({type(e).__name__})."}
        com = (idx.get("communes") or {}).get(str(code_insee).strip())
        if not com:
            return {"secteur_epfif": False, "stale": self._stale}
        interventions = com.get("interventions") or []
        niveau = max((i.get("type") for i in interventions),
                     key=lambda t: _FORCE.get(t, 0), default=None)
        return {
            "secteur_epfif": True,
            "nom": com.get("nom"),
            "niveau_max": niveau,
            "interventions": interventions,
            "source": idx.get("source"),
            "stale": self._stale,
            "note": ("Commune sous intervention EPFIF — foncier sous tension (préemption "
                     "souvent déléguée à l'EPFIF). Opérateur public à intégrer dans "
                     "l'approche, pas un terrain neutre."),
        }

    def signal(self, code_insee: Optional[str]) -> Optional[dict]:
        """Forme compacte (None si hors secteur / pas d'INSEE / source indisponible)."""
        if not code_insee:
            return None
        r = self.lookup(code_insee)
        if not r.get("secteur_epfif"):
            return None
        return {
            "niveau_max": r["niveau_max"],
            "interventions": [{k: i.get(k) for k in ("type", "partenaire", "date")}
                              for i in r["interventions"]],
        }

    def stats(self) -> dict[str, Any]:
        """Volumétrie de l'index courant (nb communes / interventions)."""
        try:
            idx = self._index()
        except Exception as e:  # noqa: BLE001
            return {"error": f"Source EPFIF indisponible ({type(e).__name__})."}
        return {"nb_communes": idx.get("nb_communes"),
                "nb_interventions": idx.get("nb_interventions"),
                "source": idx.get("source"), "stale": self._stale}
