"""La provenance d'une archive DILA se capte au téléchargement, ou elle est perdue.

Les fonds `echanges.dila.gouv.fr/OPENDATA/*` partagent une licence ouverte v2.0 qui
impose trois mentions à qui rediffuse : paternité (DILA), URL longue de
téléchargement, nom du fichier et sa date. Les deux dernières décrivent l'ARCHIVE :
une fois la ligne en base, elles ne sont plus reconstituables.

Vérifié sur les fiches officielles servies à la racine de chaque fonds
(`DILA_ACCO_Presentation_20171212.pdf`, `DILA_KALI_Presentation_20170824.pdf`) —
mêmes conditions, producteurs différents.
"""
from __future__ import annotations

import pytest

from france_opendata import acco_ingest, dila, kali_ingest


@pytest.mark.parametrize("nom,attendu", [
    ("ACCO_20260601-140000.tar.gz", "2026-06-01"),
    ("https://echanges.dila.gouv.fr/OPENDATA/ACCO/ACCO_20260601-140000.tar.gz", "2026-06-01"),
    ("KALI_20260601-140000.tar.gz", "2026-06-01"),
    # Les stocks complets suivent la même forme que les incréments.
    ("Freemium_acco_global_20250713-140000.tar.gz", "2025-07-13"),
    ("Freemium_kali_global_20250713-140000.tar.gz", "2025-07-13"),
])
def test_the_file_date_is_derived_from_its_name(nom, attendu):
    assert dila.archive_date(nom) == attendu


@pytest.mark.parametrize("nom", ["autre.tar.gz", "ACCO_2026.tar.gz", "", "ACCO_20260601.tar.gz"])
def test_an_unparseable_name_yields_no_date_rather_than_a_guess(nom):
    """Pas de date plausible fabriquée : une attribution fausse est pire qu'incomplète."""
    assert dila.archive_date(nom) is None


def test_provenance_carries_both_mentions_when_the_source_is_known():
    url = "https://echanges.dila.gouv.fr/OPENDATA/KALI/KALI_20260601-140000.tar.gz"
    assert dila.provenance(url) == {
        "source_archive_url": url, "source_archive_date": "2026-06-01"}


def test_provenance_stays_empty_rather_than_inventing_a_source():
    """Archive locale : on ignore d'où elle vient. Une ligne sans provenance se
    signale, elle ne s'invente pas une origine plausible."""
    assert dila.provenance(None) == {
        "source_archive_url": None, "source_archive_date": None}


def test_the_licence_is_shared_but_the_producer_is_not():
    """La DILA diffuse ; elle ne produit pas toujours. Confondre les deux ferait une
    attribution fausse — ACCO vient du ministère du Travail, KALI de la DILA."""
    assert "ouverte" in dila.LICENCE.lower() and "2.0" in dila.LICENCE
    assert "DILA" in dila.PATERNITE
    assert "Travail" in acco_ingest.PRODUCTEUR
    assert kali_ingest.PRODUCTEUR == dila.PATERNITE
    assert acco_ingest.PRODUCTEUR != kali_ingest.PRODUCTEUR
