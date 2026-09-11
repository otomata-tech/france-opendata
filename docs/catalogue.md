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
| `BoampClient` | BOAMP (dump DILA → parquet DuckDB) | avis de marchés publics | — · extra `[stock]` (OpenDataSoft bloquait les IP datacenter → lecture du dump DILA, issue #3 résolue) |

## 2. Immobilier, foncier, cadastre — namespace oto `foncier_*`

| Client | Source | Donne | Clé |
|---|---|---|---|
| `DvfClient` | DVF+ Cerema (depuis 2014) | transactions immobilières brutes, comparables €/m², stats commune | — |
| `DpeTertiaireClient` | DPE tertiaire ADEME (DataFair, depuis juillet 2021) | ~560 000 diagnostics NON résidentiels : secteur ERP, surface SHON, étiquettes, coordonnées **déjà en Lambert 93**, `id_rnb` | — |
| `DpeClient` | DPE ADEME (DataFair, depuis 2021) | ~15 M diagnostics énergétiques géocodés BAN (étiquette A-G, conso, GES) ; tools `foncier_dpe_*` + flag `with_dpe` sur les comparables | — |
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
| `EnedisClient` | Enedis open data | conso élec annuelle par adresse (signaux MWh) — réseau de **distribution** (BT/HTA) | — |
| `OdreClient` | ODRÉ (RTE, NaTran, Teréga) | conso élec annuelle des sites raccordés au réseau de **transport**, maille IRIS — l'étage qu'Enedis ne voit pas | — |
| `PvgisClient` | PVGIS JRC (Commission Européenne) | productible solaire annuel (point + kWc) | — |
| `BegesClient` | ADEME DataFair, `9nd9avrbto3l14md-wkode4o` | bilans GES déclarés (~11 800, dont ~7 000 obligés) — **clé SIREN**, émissions par catégorie | — |

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
| `geo.lambert93` / `resolution` | — | projeter WGS84 → Lambert 93 (stdlib) et rapprocher un SITE (adresse, point) d'un ÉTABLISSEMENT du répertoire, par la géométrie et un second signal |

---

## Hors `france-opendata` (mais open data dans l'écosystème)

- **GR** (`oto-backend/oto_mcp/tools/gr.py`) — harnais métier indépendant, `httpx` vers un service externe (pas une source open data partagée).
- **MCP data.gouv.fr** (`datagouv`, Etalab/DINUM, scope user, hébergé sans clé) — **découverte du catalogue** open data FR (search_datasets/dataservices, get_metrics…). Pas une donnée propre ; sert à trouver de nouvelles sources à brancher ici.

## Candidats identifiés (à brancher plus tard)

Repérés via le MCP data.gouv.fr (2026-06-24), par ordre d'intérêt :

| Candidat | Source | Pourquoi | Accès |
|---|---|---|---|
| **DECP** | Données Essentielles de la Commande Publique | complément/alternative à BOAMP (commande publique structurée) | data.gouv / API |
| **RNA** | Répertoire National des Associations (Min. Intérieur) | élargit l'univers entités au-delà des entreprises (~1,5 M assos loi 1901, + ARUP) | dump national / agrégé |
| **BANCO** | Base Nationale des Commerces Ouverte | commerces géolocalisés (OSM) → prospection locale | dump |
| Base Carbone | ADEME DataFair (`base-carboner`, 18 616 lignes) | facteurs d'émission GES | API REST |
| **BDNB** | CSTB — `api.bdnb.io` **répond** (vérifié 09/09/2026) | **le seul lien bâtiment → propriétaire personne morale avec SIREN** (tables `proprietaire` + `rel_batiment_groupe_proprietaire`, propriétaire principal = tri `nb_locaux_open` desc puis dédoublonnage) — aujourd'hui rien dans la lib ne relie un lieu à son propriétaire. Plus bâti, DPE, rénovation. Se joint au DPE tertiaire par `id_rnb`, jointure EXACTE et non spatiale. ⚠️ couverture **59 %** des grands bâtiments pros : les personnes physiques sont anonymisées à la source par la DGFiP (MAJIC) — à MARQUER, pas à taire | API REST |
| **IREP** | Registre des émissions polluantes — `files.georisques.fr/irep/<annee>.zip` | CO2 **par établissement**, avec SIRET et coordonnées : désigne *quel* site d'un grand compte, là où BEGES ne donne que la structure entière | ZIP annuel (`files.georisques.fr`, OK datacenter) |
| **Annuaire de l'administration** | DILA — `api-lannuaire.service-public.fr` | contacts d'administrations par SIREN **avec responsable nommé et sa fonction** ; sur cible publique, remplace un enrichissement payant | ODS ⚠️ tester l'egress |
| **Répertoire national des élus** | Min. Intérieur, data.gouv | maires par code INSEE, présidents d'EPCI — le décideur d'une commune | CSV |
| **Cartofriches** | Cerema | friches avec propriétaire publié, surface, pollution, statut — foncier dégradé, prioritaire aux AO | data.gouv CSV |
| **Registre RTE des installations de production** | ODRÉ `registre-national-installation-production-stockage-electricite-agrege` | parc électrique **nominatif** > 36 kW : énergie injectée sur 12 mois glissants, poste source, date de mise en service → « déjà équipé », détection de sous-performance, fins d'obligation d'achat 2026-2031 | ODS ⚠️ même portail qu'`odre.py`, donc même risque d'egress |
| EU ETS / EUTL | registre européen des quotas | industrie lourde sous quotas, avec exploitant et adresse | **source machine non localisée** au 09/09/2026 — l'endpoint EEA testé rend un 404 |

---

## Lacunes sur des connecteurs déjà branchés

Relevé le 11/09/2026 lors d'une revue de couverture. Ce sont des **extensions**, pas de nouveaux
namespaces — coût sans commune mesure avec un candidat ci-dessus.

- **`foncier_icpe` jette les rubriques.** Géorisques les renvoie, la whitelist `_ICPE_KEEP`
  (oto-backend, `tools/foncier.py`) ne les garde pas. Or les rubriques **2910 / 3110** (combustion)
  et **4735 / 2921** (froid) sont les meilleurs proxys de gros consommateur quand la consommation
  manque. Vérifier d'abord si le client lib remonte la fiche complète : si oui, le correctif est
  mono-fichier côté backend, sans passer par PyPI.
- **BEGES ne remonte ni les contacts ni le périmètre de consolidation.** `beges.py` demande les 22
  postes et les métadonnées. Manquent `responsable_du_suivi` / `fonction` / `telephone` / `courriel`
  (le contact énergie, nommé et gratuit), `siren_des_entites_consolidees` (la table
  `filiale → tête de groupe` en une requête, pour tout le pays) et `objectif_de_reduction_pour_2030`.
  Manque surtout la valeur dérivée qui change l'usage de la source : **poste P2.1 ÷ 0,052 kgCO2e/kWh
  = consommation électrique annuelle**. C'est la seule source publique qui relie une consommation à
  une personne morale **nommée** — Enedis à l'adresse et RTE à l'IRIS sont l'un et l'autre anonymes,
  et leur compte de points de livraison n'est qu'un compteur.

Ordre d'attaque suggéré : ces deux extensions d'abord (effet immédiat, pas de nouveau namespace),
puis IREP et le répertoire des élus (simples, pas de risque d'egress), puis l'annuaire DILA et DECP
**après** avoir testé l'egress depuis la box, et BDNB en dernier — c'est le seul vrai chantier.

## ⚠️ Gotcha : joignabilité depuis datacenter

Certaines API open data **bloquent les IP datacenter** (timeout TCP *avant* TLS — ni un souci d'URL ni d'User-Agent). Avant de brancher un connecteur, **tester l'egress depuis la box de prod**, pas seulement depuis un poste de dev.

- **Bloqué** : `*.opendatasoft.com` (ex. `boamp-datadila.opendatasoft.com`) → c'est pourquoi BOAMP lit le **dump DILA** (`echanges.dila.gouv.fr`) au lieu du portail ODS.
- **OK depuis datacenter** : DataFair ADEME (`data.ademe.fr`), Cerema (`apidf-preprod.cerema.fr`), `data.gouv.fr`, BAN (`api-adresse.data.gouv.fr`), `files.data.gouv.fr`.
