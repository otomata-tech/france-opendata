"""france-opendata — connecteurs data publique France (open data).

- EntreprisesClient : recherche-entreprises.api.gouv.fr (identité, dirigeants, finances) — sans clé
- InpiClient        : INPI/BCE (bilans, ratios) — sans clé
- BodaccClient      : BODACC (créations, ventes, procédures collectives) — sans clé
- DvfClient         : DVF Etalab (transactions immobilières, comparables) — sans clé
- BdTopoClient      : IGN BDTOPO V3 via WFS (bâti existant d'une parcelle) — sans clé
- SitadelClient     : Sit@del SDES/DiDo (permis de construire/aménager) — sans clé, fichiers nationaux à pré-fetcher
- SireneClient      : INSEE Sirene (SIRET, siège) — clé via env SIRENE_API_KEY

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

__all__ = ["EntreprisesClient", "SireneClient", "InpiClient", "BodaccClient", "DvfClient",
           "BdTopoClient", "SitadelClient"]
