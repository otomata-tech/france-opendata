"""ODRÉ — consommation électrique annuelle des sites raccordés au réseau de TRANSPORT.

Source : Opendatasoft ODRÉ, dataset `consommation-annuelle-par-iris`.
  https://odre.opendatasoft.com/explore/dataset/consommation-annuelle-par-iris/
Pas de clé. Licence Ouverte. Producteurs : RTE (électricité), NaTran et Teréga (gaz).

**Pourquoi ce client existe, alors qu'`enedis.py` couvre déjà la conso par adresse.**
Enedis est le réseau de DISTRIBUTION (BT et HTA) : au-dessus, un site est raccordé au
réseau de TRANSPORT et n'apparaît pas d'un gramme dans ses données. L'écart n'est pas
marginal, il est structurel — mesuré le 08/09/2026 :

- Saint-Jean-de-Maurienne, NAF 24 : **zéro** adresse chez Enedis, **1 702 616 MWh** ici ;
- l'adresse la plus consommatrice de toute la base Enedis plafonne à ~196 GWh, quand le
  plus gros IRIS de transport est à ~4,1 TWh.

Un ciblage « électro-intensifs » bâti sur la seule distribution est donc un ciblage sans
les électro-intensifs. Les deux étages se lisent ensemble ou pas du tout.

**Maille : l'IRIS, pas l'adresse — sauf quand il n'y a qu'un point de livraison.**
Le jeu agrège à l'IRIS. Quand `pdl_electricite_rte == 1`, il n'y a qu'un seul point de
livraison dans l'IRIS : la valeur EST celle d'un site, et l'IRIS le localise. Au-delà,
c'est une somme et le champ `maille` le dit (`site` / `iris_agrege`) — la convention de
la maison est de marquer ce qu'on ne peut pas servir, pas de le taire.

⚠️ **EGRESS — à tester depuis la box de prod AVANT de s'y fier.** Ce portail est servi
par `odre.opendatasoft.com`, et `docs/catalogue.md` documente que `*.opendatasoft.com`
**bloque les IP datacenter** (timeout TCP avant TLS — ni une URL ni un User-Agent en
cause) : c'est pour cette raison que BOAMP lit le dump DILA plutôt que son portail ODS.
Le domaine propre `opendata.reseaux-energies.fr` ne sauve rien, il redirige vers le même
hôte, et data.gouv n'héberge aucun miroir — ses sept ressources pointent toutes là.
Rien ici ne détectera le blocage : ça marche depuis un poste de dev et ça échoue en prod.

    curl -sS -m 20 -o /dev/null -w '%{http_code}\n' \
      'https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/consommation-annuelle-par-iris/records?limit=1'

Si la box est bloquée, le repli est celui que la maison applique déjà à l'IRIS INSEE :
ingérer une fois en parquet compact **bundlé dans le paquet** (cf. `insee_iris_ingest.py`).
Le jeu s'y prête — 17 891 lignes, un millésime par an, aucune fraîcheur intra-annuelle à
préserver.

Gotchas :
- `annee` est un champ **DATE**, pas une chaîne : le filtre s'écrit `year(annee)=2023`.
  `annee="2023"` rend un **400**, sans autre explication.
- **Pas de code NAF** dans ce jeu : le secteur d'activité n'existe qu'après résolution
  vers SIREN. Ne pas espérer y filtrer une division comme chez Enedis.
- Le millésime retarde d'un an sur Enedis (2023 contre 2024 au 08/09/2026) : sommer les
  deux étages mélange deux années — `annee` est rendue sur chaque signal pour que
  l'appelant puisse le dire plutôt que de l'ignorer.
- `consommation_electricite_rte` peut être `None` sur une ligne purement gazière ; ces
  lignes sont écartées, elles ne valent pas zéro.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Union

from .opendatasoft import OpendatasoftClient

PORTAL = "https://odre.opendatasoft.com"
DATASET = "consommation-annuelle-par-iris"


def _signal(r: dict[str, Any]) -> Optional[dict[str, Any]]:
    iris = r.get("code_iris")
    mwh = r.get("consommation_electricite_rte")
    annee = r.get("annee")
    if not iris or mwh is None or not annee:
        return None
    # `annee` arrive en ISO date ("2023-01-01T00:00:00+00:00") — on ne garde que l'année.
    an = str(annee)[:4]
    pdl = r.get("pdl_electricite_rte") or 0
    return {
        # Clé composite stable : un IRIS porte UNE ligne par année.
        "ref_key": f"{iris}|{an}",
        "annee": an,
        "code_iris": iris,
        "code_commune": r.get("code_insee_commune") or "",
        "nom_commune": r.get("commune") or "",
        "code_dept": r.get("code_insee_departement") or "",
        "nom_dept": r.get("departement") or "",
        "code_region": r.get("code_insee_region") or "",
        "mwh": mwh,
        "pdl": pdl,
        # Ce que la ligne EST, dit explicitement : un site, ou une somme d'IRIS.
        "maille": "site" if pdl == 1 else "iris_agrege",
        "site_unique": pdl == 1,
        "reseau": "transport",
        "geo_point": r.get("geo_point_iris"),
        "raw": r,
    }


class OdreClient:
    """Consommation annuelle des sites raccordés au réseau de transport. Sans clé."""

    def __init__(self, timeout: int = 120):
        self.timeout = timeout
        self.ods = OpendatasoftClient(PORTAL, timeout=timeout)

    def annees_disponibles(self) -> list[str]:
        """Millésimes présents, du plus récent au plus ancien.

        Le jeu retarde sur Enedis : à lire AVANT de croiser les deux étages, plutôt que
        de coder un millésime en dur qui deviendra faux en silence.
        """
        page = self.ods.records(
            DATASET, select="annee, count(*) as n", group_by="annee",
            order_by="annee desc", limit=100,
        )
        out = []
        for row in page.get("results", []):
            an = row.get("annee")
            if an:
                out.append(str(an)[:4])
        return out

    def consommation_transport(
        self,
        annee: Union[str, int],
        *,
        dept: Optional[str] = None,
        code_commune: Optional[Union[str, Iterable[str]]] = None,
        min_mwh: Optional[float] = None,
        site_unique: bool = True,
        limit: int = -1,
    ) -> dict[str, Any]:
        """Signaux de conso électrique du réseau de transport.

        `annee` : millésime (ex. 2023 ou "2023") — filtré via `year(annee)=`, le champ
        étant une date. `dept` : code INSEE. `code_commune` : un code ou une liste.
        `min_mwh` : borne basse. `site_unique=True` (défaut) ne garde que les IRIS à un
        seul point de livraison, où la ligne vaut pour un site ; le passer à False rend
        aussi les IRIS agrégés, chacun marqué `maille="iris_agrege"`.

        Retourne `{"total": int, "annee": str, "signals": [...]}`.
        """
        an = str(annee)[:4]
        parts = [f"year(annee)={int(an)}"]
        if dept:
            parts.append(f'code_insee_departement="{dept}"')
        if code_commune:
            communes = [code_commune] if isinstance(code_commune, str) else list(code_commune)
            if communes:
                inner = ", ".join(f'"{c}"' for c in communes)
                parts.append(f"code_insee_commune in ({inner})")
        if min_mwh is not None:
            parts.append(f"consommation_electricite_rte > {min_mwh}")
        if site_unique:
            parts.append("pdl_electricite_rte = 1")
        where = " and ".join(parts)
        rows = self.ods.export(DATASET, "json", where=where, limit=limit)
        signals = [s for r in rows if (s := _signal(r))]
        signals.sort(key=lambda s: s["mwh"], reverse=True)
        return {"total": len(signals), "annee": an, "signals": signals}
