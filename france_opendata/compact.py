"""Compactage des réponses entreprise et ICPE pour un agent : la lecture qu'en fait un
consommateur LLM, partagée au lieu d'être recopiée (backends d'agents).

Pur, sans I/O propre : les fonctions qui interrogent prennent les clients en paramètre
(`EntreprisesClient`, `InpiClient`, `BodaccClient`, ou tout objet de même surface — un
proxy HTTP vers le service FOD convient). Les messages servis à l'agent sont en anglais :
ce sont ceux qu'il lisait déjà.

Ce que le module garde, il le garde par listes de clés (`*_KEEP`) : une clé absente d'une
liste disparaît de la sortie. Les avertissements de la lib (`alerte`,
`postes_indisponibles`, `finances_avertissement`) y figurent exprès — une projection qui
les tairait rendrait au consommateur l'ambiguïté qu'ils lèvent.

- `compact_identity`  : fiche Recherche Entreprises réduite (siège, dirigeants, établissements).
- `rechercher`        : recherche compactée + seconde passe « forme juridique dite » + avertissement CA.
- `profil_entreprise` : identité + dernier bilan + exercices + BODACC, en parallèle, pannes partielles dites.
- `profils_entreprises` : le même, en lot (20 SIREN au plus).
- `compact_icpe`, `compact_rubriques` : fiche ICPE Géorisques réduite, rubriques « énergie » en tête.
"""
from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from .erreurs import ParametreInvalide

# --- Identité -----------------------------------------------------------------

ETABLISSEMENT_KEEP = (
    "siret", "adresse", "code_postal", "commune", "libelle_commune",
    "etat_administratif", "est_siege", "activite_principale",
    "liste_enseignes", "nom_commercial", "date_creation",
)
DIRIGEANT_KEEP = (
    "nom", "prenoms", "denomination", "siren", "qualite",
    "annee_de_naissance", "type_dirigeant",
)
IDENTITY_KEEP = (
    "siren", "nom_complet", "nom_raison_sociale", "sigle",
    "etat_administratif", "nature_juridique", "activite_principale",
    "section_activite_principale", "tranche_effectif_salarie",
    "annee_tranche_effectif_salarie", "categorie_entreprise",
    "date_creation", "date_fermeture", "site_internet",
    "nombre_etablissements", "nombre_etablissements_ouverts", "finances",
    # Posé par `finances.annotate_company` à côté de `finances` : sans lui dans la
    # liste, la projection le mangerait.
    "finances_avertissement",
)
# Seule clé de `complements` qui porte une donnée métier (la convention collective) ;
# sortie à plat — le bloc entier ramènerait une trentaine de booléens d'annuaire.
COMPLEMENT_KEEP = ("liste_idcc",)
EVENEMENT_KEEP = (
    "id", "dateparution", "familleavis", "familleavis_lib", "typeavis",
    "typeavis_lib", "tribunal", "commercant", "jugement", "registre",
    # Permalien DILA officiel, à recopier jamais reconstruire.
    "url_complete",
    # Contenu de l'annonce pour une modification et un dépôt de comptes.
    "modificationsgenerales", "depot",
)
ETABLISSEMENTS_MAX = 25
DIRIGEANTS_MAX = 10


def pick(d: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Les clés de `keys` présentes et non nulles de `d`."""
    return {k: d[k] for k in keys if k in d and d[k] is not None}


def _avec_libelle_naf(d: dict, naf_labels: Optional[Mapping[str, str]]) -> dict:
    """`activite_principale_libelle` à côté du code quand la nomenclature est fournie et
    connaît le code. Code hors NAF rév. 2 : pas de libellé plutôt qu'un faux."""
    if naf_labels:
        libelle = naf_labels.get(d.get("activite_principale") or "")
        if libelle:
            d["activite_principale_libelle"] = libelle
    return d


def compact_identity(identity: Mapping[str, Any], naf_labels: Optional[Mapping[str, str]] = None) -> dict:
    """Fiche Recherche Entreprises réduite à ce qu'un agent de prospection lit.

    Le brut pèse jusqu'à 40 000 caractères (siège à 30 champs, établissements géolocalisés).
    On garde identité, NAF, effectif, dirigeants (10), finances, et la LISTE des
    établissements (25, compactés : le test de co-localisation commune ↔ établissement
    actif en a besoin) ; `_etablissements_truncated` dit combien il y en avait au-delà.

    `naf_labels` : code NAF rév. 2 → libellé (par ex. `oto.tools.sirene.load_naf_codes()`).
    """
    out = _avec_libelle_naf(pick(identity, IDENTITY_KEEP), naf_labels)
    out.update(pick(identity.get("complements") or {}, COMPLEMENT_KEEP))
    siege = identity.get("siege")
    if isinstance(siege, Mapping):
        out["siege"] = _avec_libelle_naf(pick(siege, ETABLISSEMENT_KEEP), naf_labels)
    dirigeants = identity.get("dirigeants") or []
    out["dirigeants"] = [pick(d, DIRIGEANT_KEEP) for d in dirigeants[:DIRIGEANTS_MAX]]
    etabs = identity.get("matching_etablissements") or []
    out["etablissements"] = [_avec_libelle_naf(pick(e, ETABLISSEMENT_KEEP), naf_labels)
                             for e in etabs[:ETABLISSEMENTS_MAX]]
    if len(etabs) > ETABLISSEMENTS_MAX:
        out["_etablissements_truncated"] = len(etabs)
    return out


# --- Recherche ----------------------------------------------------------------

FILTRE_CA_AVERTISSEMENT = (
    "⚠️ `ca_min`/`ca_max` filter upstream on an amount whose unit is UNKNOWN "
    "(euros for some, thousands for others, sometimes from one year to the next for "
    "the same company) and whose 0 means \"not declared\". Measured consequences: "
    "the range lets through companies with NO known revenue (they equal 0, hence ≤ any "
    "upper bound) and misses those that filed in thousands. On "
    "`tranche_effectif_salarie=51,52,53 & ca_max=400000`, the 12 results are "
    "large companies — 11 are there only because of their 0, and the 12th is a bank at "
    "€392M read as €392k. To qualify by size, prefer "
    "`tranche_effectif_salarie` or `categorie_entreprise`, and only conclude on revenue "
    "after reading the filing (`fr_bilans`)."
)

# Formes juridiques DITES devant le nom (« la SCI Untel ») et leurs catégories INSEE.
# Le registre n'écrit presque jamais la forme dans le nom : « SCI ASC » ne ramène que
# les sociétés littéralement nommées ainsi. La forme relève d'un FILTRE.
LEGAL_FORM_CODES: dict[str, tuple[str, ...]] = {
    "SCI": ("6540", "6541", "6542", "6543", "6544"),
    "SCCV": ("6540", "6541"),
    "SCM": ("6533",),
    "SCP": ("6532",),
    "SARL": ("5499", "5485", "5410"),
    "EURL": ("5499",),
    "SAS": ("5710", "5785"),
    "SASU": ("5710",),
    "SA": ("5599", "5699"),
    "SNC": ("5202", "5203"),
}


def split_legal_form(query: Optional[str]) -> Optional[tuple[str, str]]:
    """(forme, reste) si `query` commence par une forme juridique suivie d'un nom.

    « SCI ASC » → ("SCI", "ASC") ; « SCI » seul → None (aucun nom à chercher) ;
    « ASCENSEURS » → None (préfixe non isolé)."""
    if not query:
        return None
    parts = query.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    form = parts[0].upper().strip(".")
    return (form, parts[1].strip()) if form in LEGAL_FORM_CODES and parts[1].strip() else None


def rechercher(entreprises: Any, *, query: Optional[str] = None, naf: Optional[list[str]] = None,
               departement: Optional[str] = None, code_postal: Optional[str] = None,
               commune: Optional[str] = None, employees: Optional[list[str]] = None,
               categorie_entreprise: Optional[str] = None, ca_min: Optional[int] = None,
               ca_max: Optional[int] = None, idcc: Optional[list[str]] = None,
               nature_juridique: Optional[list[str]] = None, page: int = 1, per_page: int = 25,
               naf_labels: Optional[Mapping[str, str]] = None) -> dict:
    """`entreprises.search` aux résultats compactés (`compact_identity`).

    Page 1 et sans `nature_juridique` explicite : si `query` commence par une forme dite
    (« SCI ASC »), le nom seul est aussi cherché filtré sur cette forme ; les ajouts sont
    marqués `matched_by="legal_form"` et `legal_form_retry` dit ce qui a été fait. La
    recherche littérale reste en tête : une société vraiment nommée « SCI ASC » existe.
    Un filtre `ca_min`/`ca_max` ajoute `filtre_ca_avertissement`.
    """
    def _search(q, nj, pg):
        return entreprises.search(
            query=q, naf=naf, departement=departement, code_postal=code_postal,
            commune=commune, employees=employees, categorie_entreprise=categorie_entreprise,
            ca_min=ca_min, ca_max=ca_max, idcc=idcc, nature_juridique=nj,
            page=pg, per_page=per_page,
        )

    res = _search(query, nature_juridique, page)
    form = split_legal_form(query) if page == 1 and not nature_juridique else None
    # Compacter AVANT de marquer : la projection ne garde que les clés connues.
    res["results"] = [compact_identity(r, naf_labels) for r in res.get("results", [])]
    if form:
        label, name = form
        codes = list(LEGAL_FORM_CODES[label])
        extra = _search(name, codes, 1)
        seen = {r.get("siren") for r in res["results"]}
        added = [compact_identity(r, naf_labels) for r in extra.get("results", [])
                 if r.get("siren") not in seen]
        for r in added:
            r["matched_by"] = "legal_form"
        res["results"] += added
        res["legal_form_retry"] = {
            "form": label, "query": name, "nature_juridique": codes,
            "total_results": extra.get("total_results"), "added": len(added),
        }
    if ca_min is not None or ca_max is not None:
        res["filtre_ca_avertissement"] = FILTRE_CA_AVERTISSEMENT
    return res


# --- Profil -------------------------------------------------------------------

# Sept ratios B2B du dernier dépôt + métadonnées, avec les avertissements de la liasse.
LATEST_BILAN_KEYS = (
    "date_cloture_exercice", "type_bilan",
    "chiffre_d_affaires", "resultat_net", "ebe",
    "marge_ebe", "autonomie_financiere", "taux_d_endettement",
    "ratio_de_liquidite",
    "alerte", "postes_indisponibles",
)
# Un exercice dans le survol des comptes : de quoi lire la taille et son évolution.
BILAN_OUTLINE_KEYS = (
    "date_cloture_exercice", "type_bilan", "confidentiality", "chiffre_d_affaires", "alerte",
)
PROFILS_LOT_MAX = 20

_NOTE_SANS_COMPTES = (
    "no usable accounts in the Banque de France dataset: never filed "
    "OR filed under full confidentiality (micro/small companies "
    "may keep their accounts confidential). Check for a "
    "confidential filing via the RNE records on data.inpi.fr."
)


def cloture_liquidation(events: list[Mapping[str, Any]]) -> Optional[dict]:
    """Le jugement BODACC le plus récent qui CLÔT une liquidation parmi `events` (du plus
    récent au plus ancien), ou None. Le registre SIRENE n'enregistre pas toujours la fin
    d'une société liquidée. Seule la clôture d'une liquidation compte : clore un plan de
    sauvegarde laisse une société vivante."""
    for e in events:
        if e.get("familleavis") != "collective":
            continue
        jugement = e.get("jugement") if isinstance(e.get("jugement"), Mapping) else {}
        nature = (jugement.get("nature") or "").lower()
        if "clôture" not in nature and "cloture" not in nature:
            continue
        if "insuffisance d'actif" in nature or "liquidation" in nature:
            return {k: v for k, v in {
                "nature": jugement.get("nature"),
                "date_jugement": jugement.get("date"),
                "date_parution": e.get("dateparution"),
                "id": e.get("id"),
            }.items() if v is not None}
    return None


def profil_entreprise(siren: str, *, entreprises: Any, inpi: Any, bodacc: Any,
                      naf_labels: Optional[Mapping[str, str]] = None, evenements: int = 10) -> dict:
    """Fiche d'un SIREN : identité compactée, dernier bilan (7 ratios), survol des
    exercices (`bilans_outline`, du plus récent au plus ancien), annonces BODACC récentes.

    Trois sources interrogées en parallèle. L'identité est la clé de voûte : sans elle,
    `{"error": "not_found" | "identity_unavailable", ...}`. Une panne des bilans ou du
    BODACC ne fait pas tomber la fiche : le bloc manque et `partial_errors` dit pourquoi.
    """
    partial_errors: dict[str, str] = {}

    def _safe(label, fn, *fn_args):
        try:
            return fn(*fn_args)
        except Exception as exc:  # rendue dans partial_errors, jamais tue
            partial_errors[label] = f"{type(exc).__name__}: {exc}"
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_identity = pool.submit(_safe, "identity", entreprises.get_by_siren, siren)
        f_bilans = pool.submit(_safe, "latest_bilan", inpi.list_exercises, siren)
        f_events = pool.submit(_safe, "recent_events", bodacc.search_by_siren, siren, None, evenements)

    identity = f_identity.result()
    if not identity:
        if "identity" in partial_errors:
            return {"error": "identity_unavailable", "siren": siren, "partial_errors": partial_errors}
        return {"error": "not_found", "siren": siren}

    exercises = f_bilans.result()
    latest_bilan = None
    finances_note = None
    latest_confidentiality = None
    if exercises:  # au moins un dépôt exploitable
        latest_ex = exercises[0]
        latest_confidentiality = latest_ex.get("confidentiality")
        full = _safe("latest_bilan", inpi.get_bilan, siren, latest_ex["date_cloture_exercice"])
        if full:
            latest_bilan = {k: full.get(k) for k in LATEST_BILAN_KEYS}
        if latest_confidentiality and latest_confidentiality != "Public":
            finances_note = (
                f"accounts \"{latest_confidentiality.lower()}\" (art. L.232-25) — "
                "some ratios are absent due to a confidentiality declaration"
            )
    elif exercises == []:  # succès, mais aucun dépôt exploitable
        finances_note = _NOTE_SANS_COMPTES

    events_data = f_events.result() or {}
    events = events_data.get("results", [])
    out: dict[str, Any] = {
        "siren": siren,
        "identity": compact_identity(identity, naf_labels),
        "latest_bilan": latest_bilan,
        "latest_bilan_confidentiality": latest_confidentiality,
        # None = source en panne (cf. partial_errors), [] = aucun dépôt exploitable.
        # Un CA à None reste écrit : à côté de son `alerte`, il dit « illisible », pas « absent ».
        "bilans_outline": ([{k: e[k] for k in BILAN_OUTLINE_KEYS if k in e} for e in exercises]
                           if exercises is not None else None),
        "recent_events": [pick(e, EVENEMENT_KEEP) for e in events],
        "events_total": events_data.get("total_count", 0),
    }
    cloture = cloture_liquidation(events)
    if cloture:
        out["procedure_collective_cloturee"] = cloture
        if out["identity"].get("etat_administratif") == "A":
            out["etat_administratif_avertissement"] = (
                "`identity.etat_administratif` is \"A\" (active) in the SIRENE "
                f"register, but BODACC published a judgment closing its liquidation "
                f"({cloture.get('nature')}, {cloture.get('date_jugement') or cloture.get('date_parution')}): "
                "the register has not recorded the cessation. Treat the company "
                "as closed, not as active.")
    if finances_note:
        out["finances_note"] = finances_note
    if partial_errors:
        out["partial_errors"] = partial_errors
    return out


def profils_entreprises(sirens: list[str], *, entreprises: Any, inpi: Any, bodacc: Any,
                        naf_labels: Optional[Mapping[str, str]] = None, evenements: int = 10,
                        max_workers: int = 4) -> dict:
    """`profil_entreprise` pour une liste (20 au plus), dans l'ordre reçu :
    `{profiles: [...], count}`. Un SIREN en échec devient `{error, siren}` sans faire
    tomber le lot. 4 profils en vol au plus (chacun ouvre 3 à 4 appels amont)."""
    cleaned = [str(s).strip() for s in sirens if str(s).strip()]
    if not cleaned:
        raise ParametreInvalide("sirens", "liste vide")
    if len(cleaned) > PROFILS_LOT_MAX:
        raise ParametreInvalide(
            "sirens", f"{PROFILS_LOT_MAX} au plus par appel (reçu {len(cleaned)}) — découper en lots")

    def _one(s: str) -> dict:
        try:
            return profil_entreprise(s, entreprises=entreprises, inpi=inpi, bodacc=bodacc,
                                     naf_labels=naf_labels, evenements=evenements)
        except Exception as exc:  # rendue dans la ligne du SIREN
            return {"error": f"{type(exc).__name__}: {exc}", "siren": s}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        profiles = list(pool.map(_one, cleaned))
    return {"profiles": profiles, "count": len(profiles)}


# --- ICPE (Géorisques) --------------------------------------------------------

# Rubriques dont l'activité EST une consommation d'énergie. Sert à LIRE une fiche, pas à
# mesurer : la quantité déclarée porte sur l'activité classée (m³, MW), jamais des kWh.
RUBRIQUES_ENERGIE: dict[str, str] = {
    "2910": "combustion (boilers, engines)",
    "3110": "combustion ≥ 50 MW (IED)",
    "2915": "heating by heat-transfer fluids",
    "2920": "compression and refrigeration",
    "2921": "evaporative cooling (cooling towers)",
    "4735": "ammonia — industrial cooling",
    "1185": "fluorinated refrigerants — cooling",
}
ICPE_MAX_RUBRIQUES = 10
ICPE_KEEP = (
    "raisonSociale", "siret", "adresse1", "codePostal", "codeInsee", "commune",
    "codeNaf", "longitude", "latitude", "regime", "ied", "statutSeveso",
    "prioriteNationale", "etatActivite", "codeAIOT", "serviceAIOT",
    "industrie", "carriere", "eolienne", "bovins", "porcs", "volailles",
)
ICPE_INSPECTIONS_MAX = 3


def compact_rubriques(brutes: list[Mapping[str, Any]]) -> tuple[list, list, bool]:
    """Rubriques déclarées d'une fiche ICPE, les « énergie » en tête.

    Rend `(rubriques, rubriques_energie, tronquees)`. Une grosse fiche en porte parfois
    trente : on plafonne à `ICPE_MAX_RUBRIQUES`, mais en mettant DEVANT celles qui portent
    le signal — sinon la troncature mange précisément ce qu'on cherche."""
    rubriques = [
        {
            "numero": r.get("numeroRubrique"),
            "nature": r.get("nature"),
            "regime": r.get("regimeAutoriseAlinea"),
            "quantite": r.get("quantiteTotale"),
            "unite": r.get("unite"),
        }
        for r in brutes
    ]
    energie = [
        {**r, "lecture": RUBRIQUES_ENERGIE[str(r["numero"])]}
        for r in rubriques
        if str(r.get("numero")) in RUBRIQUES_ENERGIE
    ]
    numeros_energie = {r["numero"] for r in energie}
    ordonnees = energie + [r for r in rubriques if r["numero"] not in numeros_energie]
    return (
        [{k: v for k, v in r.items() if k != "lecture"} for r in ordonnees[:ICPE_MAX_RUBRIQUES]],
        energie,
        len(ordonnees) > ICPE_MAX_RUBRIQUES,
    )


def compact_icpe(d: Mapping[str, Any]) -> dict:
    """Fiche ICPE réduite : identité, régime, IED/Seveso, géo, les 3 dernières inspections,
    rubriques résumées (`compact_rubriques`) ; `rubriques_tronquees` si plafonnées."""
    out = {k: d.get(k) for k in ICPE_KEEP}
    inspections = d.get("inspections") or []
    out["inspections"] = [
        {"date": i.get("dateInspection"),
         "url": (i.get("fichierInspection") or {}).get("urlFichier")}
        for i in inspections[-ICPE_INSPECTIONS_MAX:]
    ]
    rubriques, energie, tronquees = compact_rubriques(d.get("rubriques") or [])
    out["rubriques"] = rubriques
    out["rubriques_energie"] = energie
    if tronquees:
        out["rubriques_tronquees"] = True
    return out
