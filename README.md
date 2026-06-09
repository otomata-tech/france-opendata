# france-opendata

Connecteurs Python pour la **data publique française** (open data), extraits pour être
partagés entre projets (source unique, pas de duplication).

| Client | Source | Clé |
|---|---|---|
| `EntreprisesClient` | recherche-entreprises.api.gouv.fr (identité, dirigeants, finances) | — |
| `InpiClient` | INPI/BCE (bilans, ratios) | — |
| `BodaccClient` | BODACC (créations, ventes, procédures collectives) | — |
| `DvfClient` | DVF Etalab (transactions immobilières, comparables) | — |
| `SireneClient` | INSEE Sirene (SIRET, siège) | `SIRENE_API_KEY` (env) |

```python
from france_opendata import EntreprisesClient, DvfClient
EntreprisesClient().search(query="SCI", code_postal="94500", naf=["68.20A"])
DvfClient().stats(code_commune="94017")
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

Dépend de `requests` uniquement. Consommé par `ogic-foncier-mcp` et `oto-cli`.
