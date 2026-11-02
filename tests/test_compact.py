"""compact : la lecture agent des fiches entreprise et ICPE (hors réseau).

Deux invariants tenus partout : la projection réduit les DONNÉES, jamais les
AVERTISSEMENTS (`alerte`, `postes_indisponibles`, `finances_avertissement`) ; et
toute coupe se DIT (`_etablissements_truncated`, `rubriques_tronquees`,
`partial_errors`).
"""
from __future__ import annotations

import pytest

from france_opendata import compact
from france_opendata.erreurs import ParametreInvalide


def _company(siren, nom="X", nj="5710", **extra):
    return {"siren": siren, "nom_complet": nom, "nom_raison_sociale": nom,
            "nature_juridique": nj, "siege": {}, "dirigeants": [],
            "matching_etablissements": [], **extra}


class _Entreprises:
    def __init__(self, script=None, identity=None):
        self.script, self.identity, self.calls = script, identity, []

    def search(self, **kw):
        self.calls.append(kw)
        return self.script(kw)

    def get_by_siren(self, siren):
        if isinstance(self.identity, Exception):
            raise self.identity
        return dict(self.identity) if self.identity else None


_EXERCICES = [
    {"siren": "855200507", "date_cloture_exercice": "2024-12-31", "type_bilan": "C",
     "confidentiality": "Public", "chiffre_d_affaires": None,
     "alerte": ["valeur_indisponible", "saturation_probable"],
     "postes_indisponibles": ["0G", "0N", "1A", "AR"]},
    {"siren": "855200507", "date_cloture_exercice": "2022-12-31", "type_bilan": "C",
     "confidentiality": "Public", "chiffre_d_affaires": 5513153},
]


class _Inpi:
    def __init__(self, exercices=_EXERCICES, panne=None):
        self.exercices, self.panne = exercices, panne

    def list_exercises(self, siren):
        if self.panne:
            raise self.panne
        return [dict(e) for e in self.exercices]

    def get_bilan(self, siren, date_cloture):
        return dict(self.exercices[0], ebe=12, liasse={"FL": 1})


class _Bodacc:
    def __init__(self, results=()):
        self.results = list(results)

    def search_by_siren(self, siren, famille=None, limit=10):
        return {"results": self.results[:limit], "total_count": len(self.results)}


def _profil(identity, inpi=None, bodacc=None, **kw):
    return compact.profil_entreprise("855200507", entreprises=_Entreprises(identity=identity),
                                     inpi=inpi or _Inpi(), bodacc=bodacc or _Bodacc(), **kw)


# --- identité -----------------------------------------------------------------

def test_identite_reduite_mais_avertissements_gardes():
    bavard = _company("480470152", est_bio=True, finances={"2024": {"ca": None, "alerte": ["non_declare"]}},
                      finances_avertissement="unité non transmise",
                      complements={"liste_idcc": ["1090"], "est_qualiopi": False},
                      matching_etablissements=[{"siret": f"4804701520{i:04}", "geo": "x"} for i in range(40)],
                      dirigeants=[{"nom": f"N{i}", "date_de_naissance": "1970-01"} for i in range(12)])
    out = compact.compact_identity(bavard)
    assert "est_bio" not in out and "complements" not in out
    assert out["liste_idcc"] == ["1090"]
    assert out["finances_avertissement"] == "unité non transmise"
    assert out["finances"]["2024"]["alerte"] == ["non_declare"]
    assert len(out["etablissements"]) == 25 and out["_etablissements_truncated"] == 40
    assert "geo" not in out["etablissements"][0]
    assert len(out["dirigeants"]) == 10 and "date_de_naissance" not in out["dirigeants"][0]


def test_libelle_naf_seulement_si_la_nomenclature_le_connait():
    ident = _company("1", activite_principale="58.11Z",
                     siege={"siret": "1", "activite_principale": "99.99Z"})
    out = compact.compact_identity(ident, {"58.11Z": "Édition de livres"})
    assert out["activite_principale_libelle"] == "Édition de livres"
    assert "activite_principale_libelle" not in out["siege"]
    assert "activite_principale_libelle" not in compact.compact_identity(ident)


# --- recherche ----------------------------------------------------------------

LITTERAUX = [_company(f"87812469{i}", "SCI ASC", "6540") for i in range(2)]
CIBLE = _company("921960159", "ASC", "6540")


def test_prefixe_de_forme():
    assert compact.split_legal_form("SCI ASC") == ("SCI", "ASC")
    assert compact.split_legal_form("sarl dupont et fils") == ("SARL", "dupont et fils")
    assert compact.split_legal_form("SCI") is None
    assert compact.split_legal_form("ASCENSEURS RENOV") is None
    assert compact.split_legal_form(None) is None


def test_seconde_passe_forme_juridique_ajoutee_et_marquee():
    e = _Entreprises(lambda kw: {"results": [CIBLE], "total_results": 98} if kw["nature_juridique"]
                     else {"results": list(LITTERAUX), "total_results": 38})
    out = compact.rechercher(e, query="SCI ASC")
    sirens = [r["siren"] for r in out["results"]]
    assert sirens == [c["siren"] for c in LITTERAUX] + ["921960159"]
    assert out["results"][-1]["matched_by"] == "legal_form"
    assert out["legal_form_retry"] == {"form": "SCI", "query": "ASC",
                                       "nature_juridique": ["6540", "6541", "6542", "6543", "6544"],
                                       "total_results": 98, "added": 1}
    assert e.calls[1]["query"] == "ASC"


def test_pas_de_doublon_ni_de_seconde_passe_hors_page_1_ou_forme_explicite():
    e = _Entreprises(lambda kw: {"results": [CIBLE], "total_results": 1})
    assert compact.rechercher(e, query="SCI ASC")["legal_form_retry"]["added"] == 0
    e = _Entreprises(lambda kw: {"results": [CIBLE], "total_results": 1})
    assert "legal_form_retry" not in compact.rechercher(e, query="SCI ASC", page=2)
    assert "legal_form_retry" not in compact.rechercher(e, query="ASC", nature_juridique=["6540"])
    assert len(e.calls) == 2


def test_filtre_ca_dit_ce_qu_il_filtre():
    e = _Entreprises(lambda kw: {"results": [], "total_results": 0})
    assert compact.rechercher(e, query="x", ca_max=400000)["filtre_ca_avertissement"] == compact.FILTRE_CA_AVERTISSEMENT
    assert "filtre_ca_avertissement" not in compact.rechercher(e, query="x")


# --- profil -------------------------------------------------------------------

def test_profil_dernier_bilan_avec_ses_alertes_et_survol_des_exercices():
    out = _profil(_company("855200507"))
    assert out["latest_bilan"]["alerte"] == ["valeur_indisponible", "saturation_probable"]
    assert out["latest_bilan"]["postes_indisponibles"] == ["0G", "0N", "1A", "AR"]
    assert out["latest_bilan"]["ebe"] == 12 and "liasse" not in out["latest_bilan"]
    assert [b["chiffre_d_affaires"] for b in out["bilans_outline"]] == [None, 5513153]
    assert "postes_indisponibles" not in out["bilans_outline"][0]
    assert out["bilans_outline"][0]["alerte"] == ["valeur_indisponible", "saturation_probable"]
    assert out["latest_bilan_confidentiality"] == "Public"
    assert "partial_errors" not in out and "finances_note" not in out


def test_profil_sans_comptes_le_dit():
    out = _profil(_company("855200507"), inpi=_Inpi(exercices=[]))
    assert out["bilans_outline"] == [] and out["latest_bilan"] is None
    assert "no usable accounts" in out["finances_note"]


def test_panne_des_bilans_dite_pas_tue():
    out = _profil(_company("855200507"), inpi=_Inpi(panne=TimeoutError("parquet lent")))
    assert out["bilans_outline"] is None
    assert "parquet lent" in out["partial_errors"]["latest_bilan"]
    assert out["identity"]["siren"] == "855200507"


def test_identite_cle_de_voute():
    assert _profil(None) == {"error": "not_found", "siren": "855200507"}
    out = _profil(RuntimeError("amont"))
    assert out["error"] == "identity_unavailable" and "amont" in out["partial_errors"]["identity"]


def test_liquidation_cloturee_contredit_le_registre():
    cloture = {"familleavis": "collective", "dateparution": "2021-03-01", "id": "a1",
               "jugement": {"nature": "Jugement de clôture pour insuffisance d'actif", "date": "2021-02-15"}}
    out = _profil(_company("855200507", etat_administratif="A"),
                  bodacc=_Bodacc([{"familleavis": "dpc", "id": "z", "geo": 1}, cloture]))
    assert out["procedure_collective_cloturee"]["date_jugement"] == "2021-02-15"
    assert "Treat the company as closed" in out["etat_administratif_avertissement"]
    assert out["recent_events"][0] == {"familleavis": "dpc", "id": "z"}
    assert out["events_total"] == 2


def test_lot_dans_l_ordre_un_echec_ne_fait_pas_tomber_le_lot():
    class _E(_Entreprises):
        def get_by_siren(self, siren):
            if siren == "2":
                return None
            return _company(siren)

    out = compact.profils_entreprises([" 1", "2", "", "3"], entreprises=_E(), inpi=_Inpi(), bodacc=_Bodacc())
    assert out["count"] == 3
    assert [p["siren"] for p in out["profiles"]] == ["1", "2", "3"]
    assert out["profiles"][1] == {"error": "not_found", "siren": "2"}


def test_lot_borne():
    kw = dict(entreprises=_Entreprises(), inpi=_Inpi(), bodacc=_Bodacc())
    with pytest.raises(ParametreInvalide):
        compact.profils_entreprises([" ", ""], **kw)
    with pytest.raises(ParametreInvalide, match="20 au plus"):
        compact.profils_entreprises([str(i) for i in range(21)], **kw)


# --- ICPE ---------------------------------------------------------------------

def _rubrique(numero, nature="peu importe"):
    return {"numeroRubrique": numero, "nature": nature, "regimeAutoriseAlinea": "Déclaration",
            "quantiteTotale": "1.000", "unite": "m3"}


def test_rubriques_sortent_avec_quantite_et_unite():
    rubriques, energie, tronquees = compact.compact_rubriques([_rubrique("2663", "Pneus")])
    assert rubriques == [{"numero": "2663", "nature": "Pneus", "regime": "Déclaration",
                          "quantite": "1.000", "unite": "m3"}]
    assert energie == [] and tronquees is False


def test_la_troncature_ne_mange_pas_la_rubrique_energie():
    brutes = [_rubrique(str(4000 + i)) for i in range(compact.ICPE_MAX_RUBRIQUES + 5)]
    brutes.append(_rubrique("2910", "Combustion"))
    rubriques, energie, tronquees = compact.compact_rubriques(brutes)
    assert tronquees is True and len(rubriques) == compact.ICPE_MAX_RUBRIQUES
    assert rubriques[0]["numero"] == "2910" and "lecture" not in rubriques[0]
    assert energie[0]["lecture"] == "combustion (boilers, engines)"
    assert compact.compact_rubriques([]) == ([], [], False)


def test_fiche_icpe_reduite():
    fiche = {"raisonSociale": "USINE", "codeAIOT": "0070.1", "regime": "Autorisation",
             "documentsHorsInspection": ["gros"], "coordonneeXAIOT": 1,
             "inspections": [{"dateInspection": f"2020-0{i}-01",
                              "fichierInspection": {"urlFichier": f"u{i}"}} for i in range(1, 6)],
             "rubriques": [_rubrique(str(1000 + i)) for i in range(12)]}
    out = compact.compact_icpe(fiche)
    assert out["codeAIOT"] == "0070.1" and "documentsHorsInspection" not in out
    assert "coordonneeXAIOT" not in out
    assert [i["url"] for i in out["inspections"]] == ["u3", "u4", "u5"]
    assert out["rubriques_tronquees"] is True and len(out["rubriques"]) == 10
