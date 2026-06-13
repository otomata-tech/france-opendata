"""france-opendata — connecteurs data publique France (open data).

- EntreprisesClient : recherche-entreprises.api.gouv.fr (identité, dirigeants, finances) — sans clé
- InpiClient        : INPI/BCE (bilans, ratios) — sans clé
- BodaccClient      : BODACC (créations, ventes, procédures collectives) — sans clé
- DvfClient         : DVF Etalab (transactions immobilières, comparables) — sans clé
- BdTopoClient      : IGN BDTOPO V3 via WFS (bâti existant d'une parcelle) — sans clé
- SitadelClient     : Sit@del SDES/DiDo (permis de construire/aménager) — sans clé, fichiers nationaux à pré-fetcher
- SireneClient      : INSEE Sirene (SIRET, siège) — clé via env SIRENE_API_KEY
- GeorisquesClient  : Géorisques installations classées ICPE (régime, IED, Seveso, DREAL) — sans clé
- EnedisClient      : Enedis conso élec annuelle par adresse (signaux MWh) — sans clé
- BanClient         : Base Adresse Nationale (géocodage / reverse) — sans clé
- ApiCartoClient    : IGN API Carto cadastre (parcelle en un point/géométrie) — sans clé
- PvgisClient       : PVGIS JRC (productible solaire annuel pour un point + kWc) — sans clé
- SpectacleClient   : Licences entrepreneurs spectacles vivants (data.culture.gouv.fr) — sans clé
- OpendatasoftClient: client générique Opendatasoft Explore v2.1 (tout portail ODS public)
- FinessClient      : annuaire établissements sanitaires/médico-sociaux FINESS (data.gouv) — sans clé

Lib autonome (dépend de `requests` uniquement). Source unique partagée entre projets
(remplace la duplication des connecteurs).
"""
from .entreprises import EntreprisesClient
from .sirene import SireneClient
from .inpi import InpiClient
from .bodacc import BodaccClient
from .dvf import DvfClient
from .bdtopo import BdTopoClient
from .sitadel import SitadelClient
from .georisques import GeorisquesClient
from .enedis import EnedisClient
from .ban import BanClient
from .apicarto import ApiCartoClient
from .pvgis import PvgisClient
from .opendatasoft import OpendatasoftClient
from .culture_spectacle import SpectacleClient
from .finess import FinessClient

__all__ = ["EntreprisesClient", "SireneClient", "InpiClient", "BodaccClient", "DvfClient",
           "BdTopoClient", "SitadelClient", "GeorisquesClient",
           "EnedisClient", "BanClient", "ApiCartoClient", "PvgisClient",
           "OpendatasoftClient", "SpectacleClient", "FinessClient"]
