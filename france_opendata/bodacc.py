"""BODACC — publications légales des entreprises françaises (open data DILA).

Dataset: annonces-commerciales on OpenDataSoft v2.1.
No auth required. Licence Ouverte / Etalab 2.0.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

# Valeurs réelles du champ ODS `familleavis`. Une autre valeur ne matche AUCUNE ligne :
# elle rendrait un zéro indiscernable d'un vrai « aucune annonce » (oto#206, mesuré
# sur le LIBELLÉ « Modifications diverses », recopié de la sortie). Elle est refusée.
FAMILLES = (
    "collective", "conciliation", "creation", "divers", "dpc", "immatriculation",
    "modification", "radiation", "retablissement_professionnel", "vente",
)
# Quelques alias « parlants » côté appelant, mappés sur la valeur canonique —
# l'ancien "procedure_collective" ne matchait AUCUNE ligne.
_FAMILLE_ALIASES = {
    "procedure_collective": "collective",
    "procedures_collectives": "collective",
    "procedure": "collective",
}

# Où l'amont porte le texte de l'annonce, par famille. Le bloc `jugement` n'existe
# que pour les procédures : le lire seul taisait le texte des autres familles
# (le descriptif d'une modification dit QUOI a changé — dirigeant ou commissaire
# aux comptes, oto#206).
_TEXTE = {
    "collective": ("jugement", "complementJugement"),
    "conciliation": ("jugement", "complementJugement"),
    "retablissement_professionnel": ("jugement", "complementJugement"),
    "modification": ("modificationsgenerales", "descriptif"),
    "vente": ("acte", "descriptif"),
    "creation": ("acte", "descriptif"),
    "immatriculation": ("acte", "descriptif"),
    "radiation": ("radiationaurcs", "commentaire"),
    "dpc": ("depot", "descriptif"),
    "divers": ("divers", "contenuAnnonce"),
}

# Les blocs qui nomment l'ANCIEN titulaire d'un fonds (vente, location-gérance) : le
# `registre` d'une telle annonce porte les SIREN des deux parties, sans dire qui est qui.
_ANCIENS = {
    "listeprecedentproprietaire": "ancien_proprietaire",
    "listeprecedentexploitant": "ancien_exploitant",
}


def _famille_ods(famille: Optional[str]) -> Optional[str]:
    """La valeur ODS de `famille` (alias résolus), `None` pour toutes les familles.

    Raises:
        ValueError: famille inconnue — le message nomme les valeurs admises.
    """
    if not famille:
        return None
    canon = _FAMILLE_ALIASES.get(famille, famille)
    if canon not in FAMILLES:
        raise ValueError(
            f"famille BODACC inconnue : {famille!r}. Valeurs admises : "
            f"{', '.join(FAMILLES)} (ou aucune pour toutes). La sortie donne un "
            "libellé (ex. « Modifications diverses ») : l'entrée prend le code "
            "(`modification`)."
        )
    return canon


def _siren(numero: Any) -> Optional[str]:
    digits = str(numero).replace(" ", "")
    return digits if len(digits) == 9 and digits.isdigit() else None


def _sirens_of(registre: Any) -> list[str]:
    """Le champ `registre` est une liste type ['791195415', '791 195 415'] — et
    celle d'une vente porte les SIREN DES DEUX PARTIES. Tous les SIREN, dans l'ordre."""
    if isinstance(registre, str):
        registre = [registre]
    out: list[str] = []
    for item in registre or []:
        s = _siren(item)
        if s and s not in out:
            out.append(s)
    return out


def _bloc(rec: dict, nom: str) -> Optional[dict]:
    """Un bloc JSON de l'annonce (l'amont le sert en chaîne). `None` si absent ou
    illisible — l'appelant le marque, il ne le tait pas."""
    import json as _json

    val = rec.get(nom)
    if isinstance(val, str):
        try:
            val = _json.loads(val)
        except ValueError:
            return None
    return val if isinstance(val, dict) else None


def _anciens(rec: dict) -> Optional[dict[str, str]]:
    """SIREN → rôle (`ancien_proprietaire`, `ancien_exploitant`) des anciens
    titulaires nommés par l'annonce. `None` si l'annonce n'en porte aucun bloc lisible."""
    roles: dict[str, str] = {}
    lu = False
    for champ, role in _ANCIENS.items():
        bloc = _bloc(rec, champ)
        if bloc is None:
            continue
        lu = True
        personnes = bloc.get("personne")
        for p in personnes if isinstance(personnes, list) else [personnes]:
            num = ((p or {}).get("numeroImmatriculation") or {}).get("numeroIdentification")
            s = _siren(num) if num else None
            if s:
                roles.setdefault(s, role)
    return roles if lu else None


def _partie(siren: str, registre: list[str], anciens: Optional[dict[str, str]],
            famille: Optional[str]) -> str:
    """Le rôle du SIREN demandé dans l'annonce — `indeterminee` quand l'annonce ne
    permet pas de le dire, jamais deviné."""
    if anciens is not None:
        if siren in anciens:
            return anciens[siren]
        # L'annonce nomme ses anciens titulaires : le SEUL autre SIREN du registre est
        # le nouveau. Plusieurs autres (un ancien servi sans numéro) : on ne sait pas.
        autres = [s for s in registre if s not in anciens]
        return "nouveau_titulaire" if autres == [siren] else "indeterminee"
    if len(registre) == 1 and famille != "vente":
        return "sujet"
    return "indeterminee"


class BodaccClient:
    BASE_URL = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records"

    # ODS v2.1 : limit max 100 par page ; on borne le nombre de SIREN par
    # requête OR pour garder l'URL sous les limites serveur.
    _PAGE_LIMIT = 100
    _BATCH_CHUNK = 40

    def __init__(self, timeout: tuple[float, float] | float = DEFAULT_TIMEOUT):
        self.timeout = timeout

    def search_by_siren(
        self,
        siren: str,
        famille: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search BODACC announcements for a SIREN.

        Args:
            siren: 9-digit SIREN.
            famille: Filter by family — one of `FAMILLES`: collective (procédures
                collectives), conciliation, creation, divers, modification,
                radiation, vente, dpc (dépôt des comptes), immatriculation,
                retablissement_professionnel.
            limit: Max results.

        Raises:
            ValueError: `famille` inconnue.
        """
        clauses = [f'registre="{siren}"']
        famille = _famille_ods(famille)
        if famille:
            clauses.append(f'familleavis="{famille}"')

        resp = requests.get(self.BASE_URL, params={
            "where": " AND ".join(clauses),
            "order_by": "dateparution desc",
            "limit": str(min(limit, self._PAGE_LIMIT)),
        }, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return {
            "results": self._clean_results(data.get("results", [])),
            "total_count": data.get("total_count", 0),
        }

    def search_batch(
        self,
        sirens: list[str],
        famille: Optional[str] = None,
        chunk_size: Optional[int] = None,
    ) -> dict[str, Any]:
        """Lookup BODACC announcements for MANY SIRENs in few ODS requests.

        Purement déterministe : on récupère les annonces (champs typés), on les
        remet à plat (`annonces`), et on en dérive des COMPTES (`synthese`).
        Aucune interprétation du texte libre (ex. « en procédure collective ? ») —
        ce jugement reste à l'appelant, qui lit `texte`.

        Rattachement : le `registre` d'une vente porte les SIREN des deux parties.
        Une annonce sort en UNE LIGNE PAR SIREN DEMANDÉ qu'elle nomme (même
        `bodacc_id`), jamais au nom d'un SIREN non demandé ; `partie` dit le rôle
        de ce SIREN (`sujet`, `ancien_proprietaire`, `ancien_exploitant`,
        `nouveau_titulaire`, ou `indeterminee` quand l'annonce ne permet pas de
        le dire).

        Args:
            sirens: liste de SIREN (9 chiffres, espaces tolérés).
            famille: filtre `familleavis` optionnel (voir `FAMILLES`) — ex.
                "collective" pour ne remonter que les procédures collectives.
            chunk_size: nombre de SIREN par requête OR (défaut 40).

        Returns:
            {
              "annonces": [ {siren, partie, date_parution, date_jugement, famille,
                             famille_code, type_avis, jugement_famille,
                             jugement_nature, texte, texte_source, tribunal,
                             commercant, bodacc_id}, … ],
              "synthese": {sirens_interroges, sirens_avec_annonce,
                           sirens_sans_annonce, annonces_total, lignes_total,
                           annonces_sans_texte, annonces_sans_siren_demande,
                           par_partie, par_famille, par_type_avis,
                           par_jugement_nature, par_jugement_famille},
            }

        Raises:
            ValueError: `famille` inconnue.
        """
        norm = [s for s in (str(x).replace(" ", "") for x in sirens) if s]
        seen: set[str] = set()
        uniq = [s for s in norm if not (s in seen or seen.add(s))]

        famille = _famille_ods(famille)
        step = chunk_size or self._BATCH_CHUNK

        # Une annonce qui nomme deux SIREN demandés de deux paquets différents
        # revient deux fois : dédoublonnée par son id, sinon comptée double.
        raw: dict[Any, dict] = {}
        for i in range(0, len(uniq), step):
            for rec in self._fetch_chunk(uniq[i:i + step], famille):
                raw.setdefault(rec.get("id") or id(rec), rec)

        demandes = set(uniq)
        lignes: list[dict[str, Any]] = []
        sans_siren_demande = 0
        for rec in raw.values():
            rangs = self._retape(rec, demandes)
            if not rangs:
                sans_siren_demande += 1
            lignes.extend(rangs)
        lignes.sort(key=lambda a: a.get("date_parution") or "", reverse=True)

        return {
            "annonces": lignes,
            "synthese": self._synthese(uniq, lignes, sans_siren_demande),
        }

    def _fetch_chunk(self, sirens: list[str], famille: Optional[str]) -> list[dict]:
        """Une plage de SIREN, paginée jusqu'à épuisement (total_count > 100)."""
        or_clause = " OR ".join(f'registre="{s}"' for s in sirens)
        where = f"({or_clause})"
        if famille:
            where += f' AND familleavis="{famille}"'

        out: list[dict] = []
        offset = 0
        while True:
            resp = requests.get(self.BASE_URL, params={
                "where": where,
                "order_by": "dateparution desc",
                "limit": str(self._PAGE_LIMIT),
                "offset": str(offset),
            }, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            page = data.get("results", [])
            out.extend(page)
            offset += self._PAGE_LIMIT
            if offset >= data.get("total_count", 0) or not page:
                break
            if offset >= 10000:  # plafond d'offset ODS
                break
        return out

    def search(
        self,
        query: Optional[str] = None,
        departement: Optional[str] = None,
        famille: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search BODACC announcements by keyword / filters."""
        clauses: list[str] = []
        if query:
            clauses.append(f'search(commercant, "{query}")')
        if departement:
            clauses.append(f'numerodepartement="{departement}"')
        famille = _famille_ods(famille)
        if famille:
            clauses.append(f'familleavis="{famille}"')
        if date_from:
            clauses.append(f'dateparution>="{date_from}"')
        if date_to:
            clauses.append(f'dateparution<="{date_to}"')

        params: dict[str, str] = {
            "order_by": "dateparution desc",
            "limit": str(min(limit, self._PAGE_LIMIT)),
        }
        if clauses:
            params["where"] = " AND ".join(clauses)

        resp = requests.get(self.BASE_URL, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return {
            "results": self._clean_results(data.get("results", [])),
            "total_count": data.get("total_count", 0),
        }

    @staticmethod
    def _retape(rec: dict, demandes: set[str]) -> list[dict[str, Any]]:
        """Une annonce brute ODS → une ligne plate par SIREN DEMANDÉ qu'elle nomme."""
        registre = _sirens_of(rec.get("registre"))
        code = rec.get("familleavis")
        anciens = _anciens(rec)
        jug = _bloc(rec, "jugement") or {}
        source = _TEXTE.get(code)
        texte = ((_bloc(rec, source[0]) or {}).get(source[1]) or None) if source else None
        commun = {
            "date_parution": rec.get("dateparution"),
            "date_jugement": jug.get("date"),
            "famille": rec.get("familleavis_lib") or code,
            "famille_code": code,
            "type_avis": rec.get("typeavis_lib") or rec.get("typeavis"),
            "jugement_famille": jug.get("famille"),
            "jugement_nature": jug.get("nature"),
            "texte": texte,
            "texte_source": ".".join(source) if texte else None,
            "tribunal": rec.get("tribunal"),
            "commercant": rec.get("commercant"),
            "bodacc_id": rec.get("id"),
        }
        return [
            {"siren": s, "partie": _partie(s, registre, anciens, code), **commun}
            for s in registre if s in demandes
        ]

    @staticmethod
    def _synthese(sirens: list[str], lignes: list[dict],
                  sans_siren_demande: int = 0) -> dict[str, Any]:
        """Chiffres d'agrégation déterministes sur des champs typés.

        Les comptes par SIREN portent sur les SIREN DEMANDÉS (`sans_annonce` ne peut
        pas être négatif) ; les comptes par annonce, sur les annonces distinctes (une
        annonce qui nomme deux SIREN demandés fait deux lignes, une annonce)."""
        avec = {a["siren"] for a in lignes} & set(sirens)
        par_id: dict[Any, dict] = {}
        for a in lignes:
            par_id.setdefault(a.get("bodacc_id") or id(a), a)
        annonces = list(par_id.values())

        def compte(champ: str) -> dict[str, int]:
            return dict(Counter(a[champ] for a in annonces if a.get(champ)))

        return {
            "sirens_interroges": len(sirens),
            "sirens_avec_annonce": len(avec),
            "sirens_sans_annonce": len(sirens) - len(avec),
            "annonces_total": len(annonces),
            "lignes_total": len(lignes),
            # L'amont ne porte pas de texte pour ces annonces (ex. une création sans
            # descriptif) : dit ici, pour qu'un comptage sur `texte` sache ce qu'il rate.
            "annonces_sans_texte": sum(1 for a in annonces if not a.get("texte")),
            # Annonces rendues par l'amont sans aucun SIREN demandé à leur registre :
            # écartées des lignes, comptées ici.
            "annonces_sans_siren_demande": sans_siren_demande,
            "par_partie": dict(Counter(a["partie"] for a in lignes)),
            "par_famille": compte("famille"),
            "par_type_avis": compte("type_avis"),
            "par_jugement_nature": compte("jugement_nature"),
            "par_jugement_famille": compte("jugement_famille"),
        }

    @staticmethod
    def _clean_results(results: list[dict]) -> list[dict]:
        """Keep only non-null fields and parse JSON strings."""
        import json as _json

        cleaned = []
        for r in results:
            out: dict[str, Any] = {}
            for k, v in r.items():
                if v is None:
                    continue
                if isinstance(v, str) and v.startswith("{"):
                    try:
                        v = _json.loads(v)
                    except ValueError:
                        pass
                out[k] = v
            cleaned.append(out)
        return cleaned
