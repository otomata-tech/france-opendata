"""BODACC — cœur déterministe (offline, sans réseau).

Verrouille :
- la validation de `famille` : un alias se résout, une valeur inconnue (dont le
  LIBELLÉ que sert la sortie) est refusée en nommant les valeurs admises — jamais
  un zéro silencieux (oto#206) ;
- le rattachement d'une annonce aux SEULS SIREN demandés qu'elle nomme, avec la
  partie de chacun — le `registre` d'une vente porte les deux parties (oto#206) ;
- le texte servi pour toutes les familles, et compté quand l'amont n'en a pas ;
- la synthèse, calculée sur les SIREN demandés (jamais négative).

Les annonces de test reprennent la FORME des enregistrements ODS réels (blocs JSON
servis en chaîne) ; SIREN et noms sont fictifs.
"""
import json
import re

import pytest

from france_opendata import bodacc as mod
from france_opendata.bodacc import (FAMILLES, BodaccClient, _clauses_dates, _famille_ods,
                                    _sirens_of)

CEDANT, ACQUEREUR, TIERS = "111111111", "222222222", "333333333"


def _j(obj):
    return json.dumps(obj, ensure_ascii=False)


def _personne(siren=None, **extra):
    p = {"typePersonne": "pm", "denomination": "X", **extra}
    if siren:
        p["numeroImmatriculation"] = {"numeroIdentification": f"{siren[:3]} {siren[3:6]} {siren[6:]}",
                                      "codeRCS": "RCS", "nomGreffeImmat": "Greffe"}
    return p


def _registre(*sirens):
    out = []
    for s in sirens:
        out += [s, f"{s[:3]} {s[3:6]} {s[6:]}"]
    return out


def vente(id_, registre, anciens=None, champ="listeprecedentproprietaire"):
    rec = {"id": id_, "familleavis": "vente", "familleavis_lib": "Ventes et cessions",
           "typeavis_lib": "Avis initial", "dateparution": "2026-09-01",
           "commercant": "ACHETEUR, VENDEUR", "registre": registre,
           "acte": _j({"descriptif": "Achat d'un fonds de commerce."})}
    if anciens is not None:
        rec[champ] = _j({"personne": anciens})
    return rec


def modification(id_, siren, descriptif="Modification de l'administration."):
    return {"id": id_, "familleavis": "modification", "familleavis_lib": "Modifications diverses",
            "typeavis_lib": "Avis initial", "dateparution": "2026-08-01",
            "registre": _registre(siren),
            "modificationsgenerales": _j({"descriptif": descriptif})}


# --- famille -----------------------------------------------------------------

def test_famille_alias_maps_to_ods_value():
    assert _famille_ods("procedure_collective") == "collective"
    assert _famille_ods("procedures_collectives") == "collective"
    assert _famille_ods("collective") == "collective"
    assert _famille_ods("creation") == "creation"
    assert _famille_ods(None) is None
    assert _famille_ods("") is None


@pytest.mark.parametrize("famille", ["Modifications diverses", "depot", "modifications"])
def test_famille_inconnue_refusee_en_nommant_les_valeurs(famille):
    with pytest.raises(ValueError) as e:
        _famille_ods(famille)
    for valeur in FAMILLES:
        assert valeur in str(e.value)


def test_famille_inconnue_refusee_avant_tout_appel_amont(monkeypatch):
    def interdit(*a, **k):
        raise AssertionError("aucun appel amont attendu")
    monkeypatch.setattr(mod.requests, "get", interdit)
    c = BodaccClient()
    with pytest.raises(ValueError):
        c.search_batch([CEDANT], famille="Modifications diverses")
    with pytest.raises(ValueError):
        c.search_by_siren(CEDANT, famille="Modifications diverses")
    with pytest.raises(ValueError):
        c.search(famille="Modifications diverses")


# --- rattachement ------------------------------------------------------------

def test_sirens_of_rend_toutes_les_parties():
    assert _sirens_of(_registre(ACQUEREUR, CEDANT)) == [ACQUEREUR, CEDANT]
    assert _sirens_of("418001897") == ["418001897"]
    assert _sirens_of([]) == []
    assert _sirens_of(None) == []


def test_vente_rattachee_au_seul_siren_demande_avec_sa_partie():
    # Le registre nomme l'acquéreur EN PREMIER : l'ancien code attribuait l'annonce
    # à l'acquéreur, jamais demandé.
    rec = vente("V1", _registre(ACQUEREUR, CEDANT), anciens=_personne(CEDANT))
    [ligne] = BodaccClient._retape(rec, {CEDANT})
    assert ligne["siren"] == CEDANT
    assert ligne["partie"] == "ancien_proprietaire"
    [ligne] = BodaccClient._retape(rec, {ACQUEREUR})
    assert ligne["siren"] == ACQUEREUR
    assert ligne["partie"] == "nouveau_titulaire"


def test_vente_les_deux_parties_demandees_font_deux_lignes_une_annonce():
    rec = vente("V1", _registre(ACQUEREUR, CEDANT), anciens=_personne(CEDANT))
    lignes = BodaccClient._retape(rec, {CEDANT, ACQUEREUR})
    assert {(l["siren"], l["partie"]) for l in lignes} == {
        (CEDANT, "ancien_proprietaire"), (ACQUEREUR, "nouveau_titulaire")}
    s = BodaccClient._synthese([CEDANT, ACQUEREUR], lignes)
    assert s["annonces_total"] == 1 and s["lignes_total"] == 2
    assert s["sirens_avec_annonce"] == 2 and s["sirens_sans_annonce"] == 0
    assert s["par_famille"] == {"Ventes et cessions": 1}


def test_ancien_exploitant():
    rec = vente("V2", _registre(ACQUEREUR, CEDANT), anciens=_personne(CEDANT),
                champ="listeprecedentexploitant")
    [ligne] = BodaccClient._retape(rec, {CEDANT})
    assert ligne["partie"] == "ancien_exploitant"


def test_plusieurs_anciens_en_liste():
    rec = vente("V3", _registre(TIERS, CEDANT, ACQUEREUR),
                anciens=[_personne(CEDANT), _personne(ACQUEREUR)])
    parties = {l["siren"]: l["partie"] for l in BodaccClient._retape(rec, {CEDANT, ACQUEREUR, TIERS})}
    assert parties == {CEDANT: "ancien_proprietaire", ACQUEREUR: "ancien_proprietaire",
                       TIERS: "nouveau_titulaire"}


def test_ancien_non_inscrit_seul_siren_au_registre_est_le_nouveau():
    rec = vente("V4", _registre(ACQUEREUR), anciens={"typePersonne": "pp", "nonInscrit": "N"})
    [ligne] = BodaccClient._retape(rec, {ACQUEREUR})
    assert ligne["partie"] == "nouveau_titulaire"


def test_partie_indeterminee_quand_l_annonce_ne_le_dit_pas():
    # Ancien servi sans numéro, deux SIREN au registre : on ne devine pas.
    rec = vente("V5", _registre(ACQUEREUR, CEDANT), anciens={"typePersonne": "pp", "nonInscrit": "N"})
    assert {l["partie"] for l in BodaccClient._retape(rec, {CEDANT, ACQUEREUR})} == {"indeterminee"}
    # Vente sans bloc d'ancien titulaire.
    [ligne] = BodaccClient._retape(vente("V6", _registre(ACQUEREUR)), {ACQUEREUR})
    assert ligne["partie"] == "indeterminee"
    # Bloc illisible : pas lu, donc pas d'attribution.
    rec = vente("V7", _registre(ACQUEREUR, CEDANT))
    rec["listeprecedentproprietaire"] = "{pas du json"
    assert {l["partie"] for l in BodaccClient._retape(rec, {CEDANT})} == {"indeterminee"}


def test_annonce_a_un_seul_siren_hors_vente_est_son_sujet():
    [ligne] = BodaccClient._retape(modification("M1", CEDANT), {CEDANT})
    assert ligne["partie"] == "sujet"


# --- texte -------------------------------------------------------------------

@pytest.mark.parametrize("code,bloc,cle,source", [
    ("collective", "jugement", "complementJugement", "jugement.complementJugement"),
    ("conciliation", "jugement", "complementJugement", "jugement.complementJugement"),
    ("retablissement_professionnel", "jugement", "complementJugement", "jugement.complementJugement"),
    ("modification", "modificationsgenerales", "descriptif", "modificationsgenerales.descriptif"),
    ("vente", "acte", "descriptif", "acte.descriptif"),
    ("creation", "acte", "descriptif", "acte.descriptif"),
    ("immatriculation", "acte", "descriptif", "acte.descriptif"),
    ("radiation", "radiationaurcs", "commentaire", "radiationaurcs.commentaire"),
    ("dpc", "depot", "descriptif", "depot.descriptif"),
])
def test_texte_servi_pour_chaque_famille(code, bloc, cle, source):
    rec = {"id": "T", "familleavis": code, "familleavis_lib": "Libellé",
           "registre": _registre(CEDANT), bloc: _j({cle: "le texte"})}
    [ligne] = BodaccClient._retape(rec, {CEDANT})
    assert ligne["texte"] == "le texte"
    assert ligne["texte_source"] == source
    assert ligne["famille_code"] == code


def test_texte_absent_de_l_amont_est_compte():
    creation = {"id": "C1", "familleavis": "creation", "familleavis_lib": "Créations",
                "registre": _registre(CEDANT),
                "acte": _j({"creation": {"categorieCreation": "Immatriculation"}})}
    lignes = BodaccClient._retape(creation, {CEDANT}) + BodaccClient._retape(modification("M1", CEDANT), {CEDANT})
    sans = [l for l in lignes if l["famille_code"] == "creation"][0]
    assert sans["texte"] is None and sans["texte_source"] is None
    assert BodaccClient._synthese([CEDANT], lignes)["annonces_sans_texte"] == 1


def test_jugement_en_chaine_json_est_lu():
    raw = {
        "registre": _registre(CEDANT), "id": "A1", "dateparution": "2026-07-08",
        "familleavis": "collective", "familleavis_lib": "Procédures collectives",
        "typeavis_lib": "Avis initial", "tribunal": "TC", "commercant": "ACME",
        "jugement": _j({"famille": "Extrait de jugement", "nature": "Autre jugement et ordonnance",
                        "date": "2026-07-03", "complementJugement": "Ouvre la procedure de redressement"}),
    }
    [row] = BodaccClient._retape(raw, {CEDANT})
    assert row["siren"] == CEDANT and row["partie"] == "sujet"
    assert row["date_jugement"] == "2026-07-03"
    assert row["texte"] == "Ouvre la procedure de redressement"
    assert row["jugement_famille"] == "Extrait de jugement"
    assert row["famille"] == "Procédures collectives"


# --- lot complet, amont simulé ----------------------------------------------

class _FauxOds:
    """Rejoue la sémantique de `where` utilisée par le client sur un jeu fixe."""

    def __init__(self, records):
        self.records = records
        self.appels = 0

    def __call__(self, url, params=None, timeout=None):
        self.appels += 1
        where = params["where"]
        demandes = set(re.findall(r'registre="(\d{9})"', where))
        fam = re.search(r'familleavis="([^"]+)"', where)
        apres = re.search(r'dateparution>="([^"]+)"', where)
        avant = re.search(r'dateparution<="([^"]+)"', where)
        hits = [r for r in self.records
                if demandes & set(r.get("registre") or [])
                and (not fam or r["familleavis"] == fam.group(1))
                and (not apres or r["dateparution"] >= apres.group(1))
                and (not avant or r["dateparution"] <= avant.group(1))]
        off, lim = int(params.get("offset", 0)), int(params["limit"])

        class _R:
            def raise_for_status(self):
                pass

            def json(self_inner):
                return {"total_count": len(hits), "results": hits[off:off + lim]}
        return _R()


def test_lot_ne_rend_que_des_siren_demandes_et_une_synthese_juste(monkeypatch):
    # Cinq SIREN demandés, chacun cédant d'une vente dont l'acquéreur (non demandé)
    # est en tête du registre : l'ancien code rendait 5 SIREN jamais demandés,
    # `sirens_avec_annonce` hors des demandés et un `sans_annonce` faux.
    demandes = [f"10000000{i}" for i in range(5)]
    records = [vente(f"V{i}", _registre(f"20000000{i}", s), anciens=_personne(s))
               for i, s in enumerate(demandes[:3])]
    monkeypatch.setattr(mod.requests, "get", _FauxOds(records))
    out = BodaccClient().search_batch(demandes, famille="vente")
    assert {a["siren"] for a in out["annonces"]} == set(demandes[:3])
    assert {a["partie"] for a in out["annonces"]} == {"ancien_proprietaire"}
    s = out["synthese"]
    assert s["sirens_interroges"] == 5
    assert s["sirens_avec_annonce"] == 3
    assert s["sirens_sans_annonce"] == 2
    assert s["annonces_total"] == 3 == s["lignes_total"]
    assert s["par_partie"] == {"ancien_proprietaire": 3}
    assert s["annonces_sans_siren_demande"] == 0


def test_lot_annonce_vue_par_deux_paquets_comptee_une_fois(monkeypatch):
    rec = vente("V1", _registre(ACQUEREUR, CEDANT), anciens=_personne(CEDANT))
    faux = _FauxOds([rec])
    monkeypatch.setattr(mod.requests, "get", faux)
    out = BodaccClient().search_batch([CEDANT, ACQUEREUR], chunk_size=1)
    assert faux.appels == 2
    assert out["synthese"]["annonces_total"] == 1
    assert out["synthese"]["lignes_total"] == 2
    assert out["synthese"]["sirens_avec_annonce"] == 2


def test_lot_annonce_sans_siren_demande_ecartee_et_comptee(monkeypatch):
    # Un amont qui rendrait une annonce dont le registre ne nomme aucun SIREN demandé.
    rec = modification("M9", TIERS)
    monkeypatch.setattr(mod.BodaccClient, "_fetch_chunk", lambda self, sirens, filtres: [rec])
    out = BodaccClient().search_batch([CEDANT])
    assert out["annonces"] == []
    assert out["synthese"]["annonces_sans_siren_demande"] == 1
    assert out["synthese"]["sirens_sans_annonce"] == 1


def test_synthese_jamais_negative():
    lignes = [{"siren": CEDANT, "partie": "sujet", "bodacc_id": str(i)} for i in range(40)]
    s = BodaccClient._synthese([CEDANT, ACQUEREUR], lignes)
    assert s["sirens_avec_annonce"] == 1
    assert s["sirens_sans_annonce"] == 1


# --- fenêtre de parution -----------------------------------------------------

def test_clauses_dates_partagees_par_search_et_le_lot():
    assert _clauses_dates(None, None) == []
    assert _clauses_dates("2026-01-01", None) == ['dateparution>="2026-01-01"']
    assert _clauses_dates(None, "2026-06-30") == ['dateparution<="2026-06-30"']
    assert _clauses_dates("2026-01-01", "2026-01-01") == [
        'dateparution>="2026-01-01"', 'dateparution<="2026-01-01"']


@pytest.mark.parametrize("date_from,date_to,motif", [
    ("01/01/2026", None, "date_from invalide"),
    (None, "2026-1-5", "date_to invalide"),
    ("2026-02-30", None, "date_from invalide"),
    ("2026-W01-1", None, "date_from invalide"),
    ("2026-06-30", "2026-01-01", "fenêtre vide"),
])
def test_date_mal_formee_ou_fenetre_vide_refusee_avant_tout_appel(monkeypatch, date_from,
                                                                  date_to, motif):
    def interdit(*a, **k):
        raise AssertionError("aucun appel amont attendu")
    monkeypatch.setattr(mod.requests, "get", interdit)
    for appel in (lambda: BodaccClient().search_batch([CEDANT], date_from=date_from, date_to=date_to),
                  lambda: BodaccClient().search(date_from=date_from, date_to=date_to)):
        with pytest.raises(ValueError, match=motif):
            appel()


def test_lot_filtre_par_date_de_parution(monkeypatch):
    anciennes = modification("M0", CEDANT)
    anciennes["dateparution"] = "2019-03-01"
    recente = modification("M1", CEDANT)  # 2026-08-01
    faux = _FauxOds([anciennes, recente])
    monkeypatch.setattr(mod.requests, "get", faux)
    out = BodaccClient().search_batch([CEDANT], famille="modification",
                                      date_from="2026-01-01", date_to="2026-12-31")
    assert [a["bodacc_id"] for a in out["annonces"]] == ["M1"]
    assert out["synthese"]["periode"] == {"date_from": "2026-01-01", "date_to": "2026-12-31"}
    out = BodaccClient().search_batch([CEDANT])
    assert out["synthese"]["annonces_total"] == 2
    assert out["synthese"]["periode"] == {"date_from": None, "date_to": None}
