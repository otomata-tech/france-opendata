# Catalogue open data — vue « grosse maille »

Inventaire de toutes les sources open data publiques françaises branchées (ou
candidates) dans l'écosystème Otomata. **Grosse maille** : source + ce que ça
donne + clé + exposition oto + statut. Le détail par client vit dans son module.

> Source unique = la lib **`france-opendata`** (un client par source, sans clé sauf
> mention). Exposée dans **oto** (oto-mcp / oto-cli) par namespaces de tools.
> Re-export via oto-core (`oto.tools.*`). Mise à jour 2026-06-24.

## 1. Entreprises & légal — namespace oto `fr_*`

| Client | Source | Donne | Clé |
|---|---|---|---|
| `EntreprisesClient` | API Recherche Entreprises (api.gouv) | identité, dirigeants, finances, recherche multicritère | — |
| `SireneClient` | INSEE Sirene | SIRET, siège précis | `SIRENE_API_KEY` |
| `sirene_stock` | stock parquet INSEE (~43 M établissements, DuckDB) | lookups/bulk/énumération exhaustive | — · extra `[stock]` |
| `InpiClient` | INPI / BCE | bilans, ~13 ratios financiers | — |
| `BodaccClient` | BODACC | créations, ventes, procédures collectives | — |
| `BoampClient` | BOAMP (DILA) | avis de marchés publics | — · ⚠️ **endpoint cassé** (issue #3, OpenDataSoft bloque les IP datacenter → migrer API DILA) |

## 2. Immobilier, foncier, cadastre — namespace oto `foncier_*`

| Client | Source | Donne | Clé |
|---|---|---|---|
| `DvfClient` | DVF+ Cerema (depuis 2014) | transactions immobilières brutes, comparables €/m², stats commune | — |
| `BanClient` | Base Adresse Nationale | géocodage / reverse | — |
| `ApiCartoClient` | IGN API Carto | parcelle cadastrale (point/géométrie) | — |
| `BdTopoClient` | IGN BDTOPO V3 (WFS) | bâti d'une parcelle : emprise au sol, CES réel, hauteurs | — |
| `SitadelClient` | Sit@del SDES/DiDo | permis de construire/aménager (fichiers nationaux à pré-fetcher) | — |

## 3. Urbanisme & zonage — namespace oto `urba_*`

| Client | Source | Donne | Clé |
|---|---|---|---|
| `GpuClient` | Géoportail de l'Urbanisme | zonage PLU/PLUi, prescriptions, servitudes, URL règlement | — |
| `QpvClient` | Quartiers Prioritaires de la Ville | QPV par commune / proximité d'un point | — |
| `EpfifClient` | EPFIF (Île-de-France) | secteurs d'intervention (scrape live + cache) | — |

## 4. Risques & environnement

| Client | Source | Donne | Clé | oto |
|---|---|---|---|---|
| `GeorisquesClient` | Géorisques | ICPE (régime, IED, Seveso, DREAL) + risques naturels (GASPAR) + aléa argiles (RGA) | — | `foncier_icpe`, `urba_risques`/`urba_argiles` |

## 5. Énergie — namespace oto `foncier_*`

| Client | Source | Donne | Clé |
|---|---|---|---|
| `EnedisClient` | Enedis open data | conso élec annuelle par adresse (signaux MWh) | — |
| `PvgisClient` | PVGIS JRC (Commission Européenne) | productible solaire annuel (point + kWc) | — |

## 6. Socio-démographie & territoire — namespace oto `urba_*`

| Client | Source | Donne | Clé |
|---|---|---|---|
| `InseeMelodiClient` | INSEE Mélodi | données locales par commune (population, familles, revenus, logement) | — |

## 7. Culture — namespace oto `culture_*`

| Client | Source | Donne | Clé |
|---|---|---|---|
| `SpectacleClient` | data.culture.gouv.fr | licences entrepreneurs de spectacles vivants | — |

## 8. Santé & médico-social — namespace oto `sante_*`

| Client | Source | Donne | Clé |
|---|---|---|---|
| `FinessClient` | FINESS (data.gouv) | annuaire établissements sanitaires/médico-sociaux | — |
| `HasEssmsClient` | HAS (DuckDB/parquet) | évaluations ESSMS | — · extra `[sante]` |

## 9. Générique

| Client | Source | Donne |
|---|---|---|
| `OpendatasoftClient` | tout portail Opendatasoft Explore v2.1 | client générique (datasets ODS publics) |

---

## Hors `france-opendata` (mais open data dans l'écosystème)

- **GR** (`oto-backend/oto_mcp/tools/gr.py`) — harnais métier indépendant, `httpx` vers un service externe (pas une source open data partagée).
- **MCP data.gouv.fr** (`datagouv`, Etalab/DINUM, scope user, hébergé sans clé) — **découverte du catalogue** open data FR (search_datasets/dataservices, get_metrics…). Pas une donnée propre ; sert à trouver de nouvelles sources à brancher ici.

## Candidats identifiés (à brancher plus tard)

Repérés via le MCP data.gouv.fr (2026-06-24), par ordre d'intérêt :

| Candidat | Source | Pourquoi | Accès |
|---|---|---|---|
| **DPE ADEME** ⭐ | `data.ademe.fr` DataFair (`dpe03existant`) | **15 M diagnostics géocodés BAN** (étiquette E/F/G, conso, GES, coûts) → cross-ref direct avec DVF/foncier ; ce que les API immo payantes facturent | API REST queryable, sans clé |
| **DECP** | Données Essentielles de la Commande Publique | **vraie alternative à BOAMP** pour `fr_tenders` (issue #3) | data.gouv / API |
| **RNA** | Répertoire National des Associations (Min. Intérieur) | élargit l'univers entités au-delà des entreprises (~1,5 M assos loi 1901, + ARUP) | dump national / agrégé |
| **BANCO** | Base Nationale des Commerces Ouverte | commerces géolocalisés (OSM) → prospection locale | dump |
| **BDNB** | Base nationale des bâtiments (CSTB) | bâti + DPE + rénovation par bâtiment → complète BDTOPO | dump |
| DPE tertiaire / Base Carbone | ADEME DataFair | DPE bâtiments publics ; facteurs d'émission GES | API REST |
