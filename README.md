# france-opendata

Connecteurs Python pour la **data publique française** (open data), extraits pour être
partagés entre projets (source unique, pas de duplication).

| Client | Source | Clé |
|---|---|---|
| `EntreprisesClient` | recherche-entreprises.api.gouv.fr (identité, dirigeants, finances) | — |
| `InpiClient` | INPI/BCE (bilans, ratios) | — |
| `BodaccClient` | BODACC (créations, ventes, procédures collectives) | — |
| `BoampClient` | BOAMP (avis de marchés publics, DILA OpenDataSoft) | — |
| `acco` (module) | ACCO (accords d'entreprise, base nationale des accords collectifs, DILA, depuis 09/2017) — parser `acco.parse_acco` + crawler `acco_ingest`, extra `[stock]` (defusedxml). Stockage/recherche au consommateur (oto-backend = PostgreSQL). | — |
| `DvfClient` | DVF+ Cerema (transactions immobilières, comparables, depuis 2014) | — |
| `SireneClient` | INSEE Sirene (SIRET, siège) | `SIRENE_API_KEY` (env) |
| `BdTopoClient` | IGN BDTOPO V3 via WFS (bâti existant d'une parcelle : emprise au sol, CES réel, usages, hauteurs) | — |
| `SitadelClient` | Sit@del SDES/DiDo (permis de construire/aménager, demandeurs SIREN) — fichiers nationaux à pré-fetcher, pas d'API query-able | — |
| `FrenchTechClient` | Capitale French Tech (WordPress REST live) : annuaire écosystème (startups/structures/prestataires, dirigeant+email+tel), événements, appels à projet, financements + French Tech Central (Synbird) — défaut Aix-Marseille, `base_url` paramétrable | — |

```python
from france_opendata import EntreprisesClient, DvfClient
EntreprisesClient().search(query="SCI", code_postal="94500", naf=["68.20A"])
DvfClient().stats(code_commune="94017")
# bâti d'une parcelle (géométrie GeoJSON du cadastre) → emprise au sol, CES réel
BdTopoClient().bati_parcelle(parcelle_geometry, contenance_m2=2493)
# Sit@del : download du fichier national puis itération normalisée filtrée
from france_opendata.sitadel import RID_LOGEMENTS
c = SitadelClient()
c.download(RID_LOGEMENTS, "/tmp/logements.csv")
permis = list(c.iter_permis("/tmp/logements.csv", kind="logements", depts={"94"}))
```

## Clé Sirene (optionnelle)

Seul `SireneClient` (SIRET / siège précis) nécessite une clé INSEE. **Tout le reste fonctionne sans
clé** — pour les données société, `EntreprisesClient` (recherche-entreprises) couvre l'essentiel.

Obtenir la clé (gratuite) :
1. Créer un compte sur le **portail API de l'INSEE** : https://portail-api.insee.fr/
2. Souscrire à l'API **« Sirene »** (catalogue) et générer une **clé d'intégration**.
3. La fournir via l'environnement ou le constructeur :

```bash
export SIRENE_API_KEY="votre_cle"
```
```python
from france_opendata import SireneClient
SireneClient().get_siret("39860733300059")      # lit SIRENE_API_KEY depuis l'env
SireneClient(api_key="…").get_headquarters("398607333")
```

> **Catalogue complet** (toutes les sources par domaine, vue « grosse maille » + candidats à brancher) : [`docs/catalogue.md`](docs/catalogue.md).

Dépend de `requests` uniquement. Consommé par `ogic-foncier-mcp` et `oto-cli`.
