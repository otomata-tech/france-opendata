"""Règlement écrit d'un PLU/PLUi : résolution du PDF par idurba (GPU) et garde-fous du
téléchargement (taille, délai, contenu). Hors réseau : sessions HTTP simulées."""
from __future__ import annotations

import time

import pytest

from france_opendata import gpu, reglement
from france_opendata.erreurs import ParametreInvalide
from france_opendata.gpu import GpuClient, ReglementIntrouvable


@pytest.mark.parametrize("idurba, partition", [
    ("80021_PLU_20251211", "DU_80021"),
    ("200046977_PLUI_20260326", "DU_200046977"),
    ("200084952_PLUi_20260212", "DU_200084952"),
    ("200054807_PLUi_20260306_B", "DU_200054807_B"),
    ("31555_PSMV_20250217", "PSMV_31555"),
    ("2A004_PLU_20200101", "DU_2A004"),
])
def test_partition_deduite_de_l_idurba(idurba, partition):
    assert GpuClient.partition_of(idurba) == partition


@pytest.mark.parametrize("idurba", ["", "80021", "80021_PLU", "80021_SCOT_20200101",
                                    "../../etc_PLU_20200101", "80021_PLU_2025121"])
def test_idurba_mal_forme_refuse_en_nommant_le_parametre(idurba):
    with pytest.raises(ParametreInvalide) as e:
        GpuClient.partition_of(idurba)
    assert e.value.parametre == "idurba"


class _Reponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._payload


def _client(monkeypatch, documents, pieces):
    c = GpuClient(retries=1)
    appels = []

    def get(url, params=None, timeout=None):
        appels.append((url, params))
        if url.endswith("/document"):
            return _Reponse({"features": [{"properties": d} for d in documents]})
        return _Reponse(pieces)
    monkeypatch.setattr(c._session, "get", get)
    return c, appels


DOC_LYON = {"name": "200046977_PLUi_20260326", "gpu_doc_id": "4f3d", "du_type": "PLUi",
            "grid_title": "PLUI GRAND LYON LA METROPOLE", "partition": "DU_200046977"}
PIECES = [{"name": "200046977_reglement_20260326.pdf", "title": "Règlement écrit", "path": "Règlements"},
          {"name": "200046977_reglement_graphique_1_20260326.pdf", "title": "Règlement graphique 1",
           "path": "Règlements"},
          {"name": "200046977_PPRT_reglement_20260326.pdf", "title": "PPRT reglement", "path": "Annexes"}]


def test_reglement_ecrit_resolu_depuis_le_document_publie(monkeypatch):
    c, appels = _client(monkeypatch, [DOC_LYON], PIECES)

    r = c.reglement_ecrit("200046977_PLUI_20260326")  # casse du zonage ≠ casse du document

    assert appels[0][1] == {"partition": "DU_200046977"}
    assert r["gpu_doc_id"] == "4f3d" and r["du_type"] == "PLUi"
    assert r["titre"] == "PLUI GRAND LYON LA METROPOLE"
    assert r["fichiers"] == [{
        "nom": "200046977_reglement_20260326.pdf",
        "url": "https://data.geopf.fr/annexes/gpu/documents/DU_200046977/4f3d/"
               "200046977_reglement_20260326.pdf"}]
    assert r["autres_documents"] == []


def test_document_absent_ne_substitue_pas_un_autre(monkeypatch):
    nouveau = dict(DOC_LYON, name="200046977_PLUi_20270101", gpu_doc_id="9999")
    c, _ = _client(monkeypatch, [nouveau], PIECES)

    with pytest.raises(ReglementIntrouvable) as e:
        c.reglement_ecrit("200046977_PLUI_20260326")
    assert e.value.autres_documents == ["200046977_PLUi_20270101"]
    assert "200046977_PLUi_20270101" in str(e.value)


def _piece(nom):
    return {"name": nom, "title": "Règlement écrit", "path": "Règlements"}


def test_reglements_des_procedures_anterieures_ecartes(monkeypatch):
    # Vu sur Saint-Valery-sur-Somme : six pièces « Règlement écrit » de 2005 à 2019.
    doc = dict(DOC_LYON, name="80721_PLU_20190415", gpu_doc_id="bd27", du_type="PLU")
    c, _ = _client(monkeypatch, [doc], [_piece("80721_reglement_20050608.pdf"),
                                        _piece("80721_reglement_20190415.pdf"),
                                        _piece("80721_reglement_20120227.pdf")])

    r = c.reglement_ecrit("80721_PLU_20190415")

    assert [f["nom"] for f in r["fichiers"]] == ["80721_reglement_20190415.pdf"]
    assert r["pieces_ecartees"] == ["80721_reglement_20050608.pdf", "80721_reglement_20120227.pdf"]


def test_plusieurs_tomes_a_la_date_du_document_tous_retenus(monkeypatch):
    doc = dict(DOC_LYON, name="80021_PLU_20251211", du_type="PLU")
    c, _ = _client(monkeypatch, [doc], [_piece("80021_reglement_tome2_20251211.pdf"),
                                        _piece("80021_reglement_tome1_20251211.pdf")])

    r = c.reglement_ecrit("80021_PLU_20251211")

    assert [f["nom"] for f in r["fichiers"]] == ["80021_reglement_tome1_20251211.pdf",
                                                 "80021_reglement_tome2_20251211.pdf"]


def test_plusieurs_pieces_aucune_datee_ambigu(monkeypatch):
    doc = dict(DOC_LYON, name="80021_PLU_20251211", du_type="PLU")
    c, _ = _client(monkeypatch, [doc], [_piece("80021_reglement_20100101.pdf"),
                                        _piece("80021_reglement_20150101.pdf")])

    with pytest.raises(ReglementIntrouvable, match="aucune datée du document"):
        c.reglement_ecrit("80021_PLU_20251211")


def test_reglement_ecrit_reconnu_a_son_nom_cnig_quand_l_intitule_est_libre(monkeypatch):
    # PLUi Aix-Marseille-Provence, volet A (Marseille) : le GPU intitule la pièce
    # « L - Règlement - M - Règlement de zones spécifiques », pas « Règlement écrit ».
    doc = {"name": "200054807_PLUi_20260803_A", "gpu_doc_id": "3d08", "du_type": "PLUi",
           "grid_title": "PLUI AIX-MARSEILLE-PROVENCE", "partition": "DU_200054807_A"}
    c, _ = _client(monkeypatch, [doc], [
        {"name": "200054807_rapport_1_20260803_A.pdf",
         "title": "B1 - Rapport de présentation - Introduction générale", "path": "Rapport de présentation"},
        {"name": "200054807_prescription_surf_05_01_20260803_A.pdf",
         "title": "O1 - Liste des emplacements réservés pour voirie", "path": "Règlements"},
        {"name": "200054807_reglement_20260803_A.pdf",
         "title": "L - Règlement - M - Règlement de zones spécifiques", "path": "Règlements"},
        {"name": "200054807_reglement_graphique_0_20260803_A.pdf",
         "title": "P0 - Tableau d'assemblage du PLUi", "path": "Règlements"},
        {"name": "200054807_PPRT_reglement_20260803_A.pdf", "title": "PPRT", "path": "Annexes"},
        {"name": "13055_reglement_20260803_A.pdf", "title": "Règlement communal", "path": "Annexes"},
    ])

    r = c.reglement_ecrit("200054807_PLUI_20260803_A")  # casse du zonage

    assert r["fichiers"] == [{
        "nom": "200054807_reglement_20260803_A.pdf",
        "url": "https://data.geopf.fr/annexes/gpu/documents/DU_200054807_A/3d08/"
               "200054807_reglement_20260803_A.pdf"}]
    assert r["pieces_ecartees"] == []


@pytest.mark.parametrize("nom, attendu", [
    ("80021_reglement_2_20251211.pdf", True),
    ("80021_reglement_tome1_20251211.pdf", True),
    ("80021_reglement_graphique_1_20251211.pdf", False),
    ("80021_reglement_annexe_20251211.pdf", False),
    ("80021_PPRT_reglement_20251211.pdf", False),
    ("80022_reglement_20251211.pdf", False),     # un autre territoire
])
def test_nom_cnig_du_reglement_ecrit(nom, attendu):
    assert gpu._est_reglement_ecrit({"name": nom, "title": "Pièce"}, "80021") is attendu


def test_document_sans_reglement_ecrit(monkeypatch):
    c, _ = _client(monkeypatch, [dict(DOC_LYON, name="80021_CC_20200101")],
                   [{"name": "80021_rapport_20200101.pdf", "title": "Rapport", "path": None}])

    with pytest.raises(ReglementIntrouvable, match="ne publie pas de règlement écrit"):
        c.reglement_ecrit("80021_CC_20200101")


# --- téléchargement ----------------------------------------------------------

class _Flux:
    def __init__(self, chunks, headers=None, status=200):
        self._chunks, self.headers, self.status_code = chunks, headers or {}, status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, _n):
        yield from self._chunks


def test_taille_annoncee_au_dela_du_plafond_refusee_sans_telecharger(monkeypatch, tmp_path):
    monkeypatch.setattr(reglement.requests, "get",
                        lambda *a, **k: _Flux([b"%PDF" + b"x" * 10], {"Content-Length": str(300_000_000)}))
    with pytest.raises(reglement.ReglementTropVolumineux):
        reglement.download_pdf("https://x/r.pdf", tmp_path / "r.pdf", max_bytes=200_000_000)


def test_taille_reelle_au_dela_du_plafond_refusee(monkeypatch, tmp_path):
    monkeypatch.setattr(reglement.requests, "get",
                        lambda *a, **k: _Flux([b"%PDF" + b"x" * 60, b"y" * 60]))
    with pytest.raises(reglement.ReglementTropVolumineux):
        reglement.download_pdf("https://x/r.pdf", tmp_path / "r.pdf", max_bytes=100)


def test_page_html_servie_en_200_n_est_pas_un_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(reglement.requests, "get", lambda *a, **k: _Flux([b"<html>404</html>"]))
    with pytest.raises(reglement.PasUnPdf):
        reglement.download_pdf("https://x/r.pdf", tmp_path / "r.pdf")


def test_echeance_depassee(monkeypatch, tmp_path):
    monkeypatch.setattr(reglement.requests, "get", lambda *a, **k: _Flux([b"%PDF-1.7"]))
    with pytest.raises(reglement.DelaiDepasse):
        reglement.download_pdf("https://x/r.pdf", tmp_path / "r.pdf", echeance=time.monotonic() - 1)


def test_plusieurs_pieces_mises_bout_a_bout(monkeypatch):
    textes = {"https://x/a.pdf": ("A" * 3000, False), "https://x/b.pdf": ("", True)}

    def faux(url, **_k):
        t, sc = textes[url]
        return {"text": t, "chars": len(t), "scanne_probable": sc, "size_mo": 1.0}
    monkeypatch.setattr(reglement, "fetch_and_parse", faux)

    r = reglement.fetch_and_parse_all(list(textes), delai_s=60)

    assert r["text"].startswith("=== a.pdf ===\nAAA") and "=== b.pdf ===" in r["text"]
    assert r["scanne_probable"] is True  # une pièce illisible : le règlement ne l'est pas en entier
    assert r["size_mo"] == 2.0 and [f["scanne_probable"] for f in r["fichiers"]] == [False, True]


# --- lecture : chapitre de zone, passages -------------------------------------

TEXTE = """SOMMAIRE
ZONE UA …12
ZONE UB …30
DISPOSITIONS GÉNÉRALES
La hauteur est mesurée à l'égout du toit.
ZONE UA
La zone UA correspond au centre historique.
Secteur UAa : hauteur maximale 15 mètres.
zone A ou N du fait de leur incompatibilité
Article UA 10 – HAUTEUR MAXIMALE
En UA, la hauteur ne peut excéder 12 mètres.
ZONE UB B
Hauteur maximale en UB : 9 mètres.
Zone A 2
En A2, hauteur 7 mètres.
Dispositions applicables à la zone Ub
Dans la zone Ub2, hauteur maximale 8 mètres.
QUALITÉ URBAINE""".splitlines()


def test_le_secteur_sans_chapitre_propre_rend_celui_de_sa_zone():
    s = reglement.section_zone(TEXTE, "UAa")
    assert s["zone_chapitre"] == "UA" and s["zone_demandee"] == "UAa"
    assert TEXTE[s["ligne_debut"]] == "ZONE UA"           # pas l'entrée du sommaire
    assert TEXTE[s["ligne_fin"]] == "ZONE UB B"           # ni la phrase « zone A ou N… »


def test_indice_separe_par_pdftotext():
    s = reglement.section_zone(TEXTE, "A2")
    assert s["zone_chapitre"] == "A2" and TEXTE[s["ligne_fin"]].startswith("Dispositions")


def test_titre_dispositions_applicables_et_fin_de_document():
    s = reglement.section_zone(TEXTE, "Ub2")
    assert s["zone_chapitre"] == "Ub" and s["ligne_fin"] == len(TEXTE)


def test_zone_sans_chapitre_reconnu_rend_none():
    assert reglement.section_zone(TEXTE, "UX") is None
    assert reglement.section_zone(TEXTE, "ua") is None    # le code de zone garde sa casse


def test_passages_dans_le_chapitre_casse_et_accents_replies():
    s = reglement.section_zone(TEXTE, "UA")
    n, p = reglement.passages(TEXTE, query="hauteur maximale", debut=s["ligne_debut"],
                              fin=s["ligne_fin"], context_lignes=1)
    assert n == 2 and all("UB" not in x for x in p)
    assert "HAUTEUR MAXIMALE" in p[1]


def test_passages_zone_en_mot_entier_et_sans_doublon():
    n, p = reglement.passages(TEXTE, zone="UA", context_lignes=10)
    assert n == 5                                         # UAa n'est pas UA
    assert len(p) == 1                                    # occurrences couvertes : un passage
