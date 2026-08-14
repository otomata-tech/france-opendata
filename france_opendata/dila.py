"""Ce que les fonds DILA ont en commun : leur licence, et la forme de leurs archives.

Les jeux publiés sur `echanges.dila.gouv.fr/OPENDATA/` (ACCO, KALI, BODACC, LEGI,
JURI…) partagent la même fiche type et les mêmes conditions de rediffusion —
vérifiées sur les fiches officielles servies à la racine de chaque fonds, pas
supposées :

> Les données sont réutilisables gratuitement sous licence ouverte v2.0. Les
> réutilisateurs s'obligent à mentionner : la paternité des données (DILA) ;
> l'URL d'accès longue de téléchargement ; le nom du fichier téléchargé ainsi que
> la date du fichier.

Deux de ces trois mentions décrivent l'ARCHIVE, pas le jeu de données : elles sont à
capter au moment du téléchargement, ou perdues pour toujours. D'où `archive_date` et
les colonnes de provenance que chaque crawler reporte sur ses lignes.

Ce qui n'est PAS commun : le **producteur**. La DILA diffuse, mais la donnée vient
parfois d'ailleurs (ACCO est produit par la Direction Générale du Travail, KALI par
la DILA elle-même). Chaque module de fonds déclare donc son `PRODUCTEUR` ; seule la
paternité de diffusion est ici.
"""
from __future__ import annotations

import re
from typing import Optional

BASE_URL = "https://echanges.dila.gouv.fr/OPENDATA"

LICENCE = "Licence Ouverte v2.0 (Etalab)"
PATERNITE = "Direction de l'information légale et administrative (DILA)"

# Toutes les archives DILA suivent `…_YYYYMMDD-HHMMSS.tar.gz` — incréments comme
# stocks complets (`Freemium_<fonds>_global_…`).
_ARCHIVE_DATE_RE = re.compile(r"_(\d{4})(\d{2})(\d{2})-\d{6}\.tar\.gz$")


def archive_date(name_or_url: str) -> Optional[str]:
    """`ACCO_20260601-140000.tar.gz` → `2026-06-01`. None si le nom ne la porte pas.

    None plutôt qu'une date approchée : une attribution fausse est pire qu'une
    attribution incomplète.
    """
    m = _ARCHIVE_DATE_RE.search(name_or_url or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def provenance(source: Optional[str]) -> dict[str, Optional[str]]:
    """Les colonnes de provenance à reporter sur chaque ligne d'une archive.

    `source` = l'URL longue de téléchargement, ou None quand on ne la connaît pas
    (archive locale) — auquel cas rien n'est inventé.
    """
    return {"source_archive_url": source, "source_archive_date": archive_date(source or "")}
