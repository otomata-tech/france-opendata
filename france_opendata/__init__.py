"""france-opendata — connecteurs data publique France (open data).

- EntreprisesClient : recherche-entreprises.api.gouv.fr (identité, dirigeants, finances) — sans clé
- InpiClient        : INPI/BCE (bilans, ratios) — sans clé
- BodaccClient      : BODACC (créations, ventes, procédures collectives) — sans clé
- BoampClient       : BOAMP (avis de marchés publics) — parquet DuckDB via dump DILA, extra [stock]
- DvfClient         : DVF+ Cerema (transactions immobilières, comparables, depuis 2014) — sans clé
- DpeClient         : DPE ADEME (diagnostics perf énergétique logements, étiquettes, depuis 2021) — sans clé
- BdTopoClient      : IGN BDTOPO V3 via WFS (bâti existant d'une parcelle) — sans clé
- SitadelClient     : Sit@del SDES/DiDo (permis de construire/aménager) — sans clé, fichiers nationaux à pré-fetcher
- SireneClient      : INSEE Sirene (SIRET, siège) — clé via env SIRENE_API_KEY
- GeorisquesClient  : Géorisques — ICPE (régime, IED, Seveso, DREAL) + risques naturels d'une commune (GASPAR) + aléa argiles (RGA) — sans clé
- EnedisClient      : Enedis conso élec annuelle par adresse (signaux MWh) — sans clé
- BanClient         : Base Adresse Nationale (géocodage / reverse) — sans clé
- ApiCartoClient    : IGN API Carto cadastre (parcelle en un point/géométrie) — sans clé
- PvgisClient       : PVGIS JRC (productible solaire annuel pour un point + kWc) — sans clé
- GpuClient         : Géoportail de l'Urbanisme (zonage PLU/PLUi d'un point, prescriptions, servitudes, URL règlement) — sans clé
- QpvClient         : Quartiers Prioritaires de la Ville (dataset national, par commune / proximité d'un point) — sans clé
- InseeMelodiClient : INSEE Mélodi — données locales par commune (population, familles, revenus, logement) — sans clé
- InseeIrisClient   : INSEE recensement à l'IRIS (quartier) — parquet bundlé RP 2021, par IRIS ou par commune/arrondissement — extra [stock]
- EpfifClient       : secteurs d'intervention EPFIF (Île-de-France) — scrape live de la page cartographie + cache TTL — sans clé
- SpectacleClient   : Licences entrepreneurs spectacles vivants (data.culture.gouv.fr) — sans clé
- OpendatasoftClient: client générique Opendatasoft Explore v2.1 (tout portail ODS public)
- FinessClient      : annuaire établissements sanitaires/médico-sociaux FINESS (data.gouv) — sans clé
- HasEssmsClient    : évaluations ESSMS (HAS, DuckDB sur parquet) — sans clé, extra [sante]
- acco (module)     : accords d'entreprise (base nationale des accords collectifs, DILA, depuis 09/2017) — parser `acco.parse_acco` + crawler `acco_ingest`, extra [stock] (defusedxml). Stockage/recherche au consommateur (oto-backend = PostgreSQL).

Lib autonome (dépend de `requests` uniquement). Source unique partagée entre projets
(remplace la duplication des connecteurs).
"""
from .entreprises import EntreprisesClient
from .sirene import SireneClient
from .inpi import InpiClient
from .bodacc import BodaccClient
from .boamp import BoampClient
from .dvf import DvfClient
from .dpe import DpeClient
from .bdtopo import BdTopoClient
from .sitadel import SitadelClient
from .georisques import GeorisquesClient
from .enedis import EnedisClient
from .ban import BanClient
from .apicarto import ApiCartoClient
from .pvgis import PvgisClient
from .gpu import GpuClient
from .qpv import QpvClient
from .insee_melodi import InseeMelodiClient
from .insee_iris import InseeIrisClient  # import lazy de duckdb (extra [stock]) dans _connect
from .epfif import EpfifClient
from .opendatasoft import OpendatasoftClient
from .culture_spectacle import SpectacleClient
from .finess import FinessClient
from .has_essms import HasEssmsClient  # import lazy de duckdb (extra [sante]) dans _connect
from .egapro import EgaproClient
from .frenchtech import FrenchTechClient
# ACCO = parser + crawler (pas de client de requête) : importer `france_opendata.acco`
# (parse_acco) et `france_opendata.acco_ingest` directement.

__all__ = ["EntreprisesClient", "SireneClient", "InpiClient", "BodaccClient", "BoampClient", "DvfClient", "DpeClient",
           "BdTopoClient", "SitadelClient", "GeorisquesClient",
           "EnedisClient", "BanClient", "ApiCartoClient", "PvgisClient",
           "GpuClient", "QpvClient", "InseeMelodiClient", "InseeIrisClient", "EpfifClient",
           "OpendatasoftClient", "SpectacleClient", "FinessClient", "HasEssmsClient",
           "EgaproClient", "FrenchTechClient"]
