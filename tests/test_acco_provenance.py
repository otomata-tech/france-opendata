"""La provenance de l'archive DILA se capte à l'ingestion, ou elle est perdue.

La licence de rediffusion (ouverte v2.0) impose de mentionner l'URL longue de
téléchargement, le nom du fichier et sa date. Deux de ces trois mentions décrivent
l'ARCHIVE, pas le jeu de données : une fois la ligne en base, elles ne sont plus
reconstituables.
"""
from __future__ import annotations

import pytest

from france_opendata import acco_ingest as ing


@pytest.mark.parametrize("nom,attendu", [
    ("ACCO_20260601-140000.tar.gz", "2026-06-01"),
    ("https://echanges.dila.gouv.fr/OPENDATA/ACCO/ACCO_20260601-140000.tar.gz", "2026-06-01"),
    # Le stock complet suit la même forme — la règle vaut pour les deux flux.
    ("Freemium_acco_global_20250713-140000.tar.gz", "2025-07-13"),
])
def test_the_file_date_is_derived_from_its_name(nom, attendu):
    assert ing.archive_date(nom) == attendu


@pytest.mark.parametrize("nom", ["autre.tar.gz", "ACCO_2026.tar.gz", "", "ACCO_20260601.tar.gz"])
def test_an_unparseable_name_yields_no_date_rather_than_a_guess(nom):
    """Pas de date plausible fabriquée : une attribution fausse est pire qu'incomplète."""
    assert ing.archive_date(nom) is None


def test_the_licence_mentions_are_available_to_whoever_redistributes():
    """Les trois mentions imposées doivent être servables par un consommateur."""
    assert "ouverte" in ing.LICENCE.lower() and "2.0" in ing.LICENCE
    assert "DILA" in ing.PATERNITE
    assert "Travail" in ing.PRODUCTEUR
