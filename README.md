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

Dépend de `requests` uniquement. Consommé par `ogic-foncier-mcp` (et destiné à remplacer
les copies dans `oto-cli`).
