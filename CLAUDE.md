# france-opendata

Lib Python des connecteurs de **données publiques françaises**, source unique partagée
entre projets (service FOD, backend oto, CLI, apps tierces). Publiée sur PyPI. La liste
des clients est dans `README.md` ; ce fichier porte ce qu'il ne dit pas.

## Stack

`requests` seul en base. Extras à la carte : `[stock]` (DuckDB + defusedxml — parquets
SIRENE/BOAMP, parseurs XML DILA durcis), `[sante]` (DuckDB). Sans extra, seuls les
clients HTTP sont disponibles — les imports lourds sont **lazy**, à l'intérieur des
fonctions, pour que ça reste vrai.

## Architecture

```
france_opendata/
  <client>.py          # un fichier par source (entreprises, inpi, bodacc, dvf, dpe…)
  *_ingest.py          # crawlers des dumps DILA (acco, kali, legi, juri, boamp)
  sirene_stock.py      # requêtes DuckDB sur le parquet partitionné
  ── honnêteté de la donnée ──
  alertes.py           # le vocabulaire FERMÉ des alertes + la certitude de chaque code
  finances.py          # bloc `finances` de Recherche Entreprises (le 0 code l'absence)
  liasse.py            # liasse INPI (la sentinelle INT32 dit deux choses)
  dila.py              # licence, paternité et provenance communes aux fonds DILA
```

## Commands

```bash
.venv/bin/python -m pytest -q          # 99 tests, tous hors réseau
# Publier (hatch ne marche pas ici) :
python3 -m venv /tmp/be && /tmp/be/bin/pip install build twine
git archive HEAD | tar -x -C <dir> && cd <dir> && /tmp/be/bin/python -m build
TWINE_USERNAME=__token__ TWINE_PASSWORD="$(sops -d --extract '["PYPI_TOKEN"]' ~/.otomata/secrets/secrets.yaml)" \
  /tmp/be/bin/twine upload dist/*
```

Builder depuis `git archive HEAD` : sinon le WIP non commité part dans le paquet.

## Conventions

- **Une valeur qu'on ne peut pas servir se MARQUE, elle ne se corrige ni ne se tait.**
  Convertir un montant dont on ignore l'unité serait indétectable en aval — pire que
  l'absence. Les annotateurs retirent ce qui n'est pas une donnée et signalent le reste.
- **Chaque code d'alerte porte sa certitude** (`alertes.ALERTES`) : `prouve` = lu dans la
  donnée, `infere` = déduit, et le NOM le dit (`saturation_probable`). Surestimer sa
  confiance serait le mensonge qu'on corrige, un cran plus haut — **et le sous-estimer
  tait un fait qu'on possède**.
- **La forme est ADDITIVE.** Un poste illisible sort de `liasse` (y réinjecter la
  sentinelle ferait un CA de 2 147 483 647, franc et inventé) et son code part dans
  `postes_indisponibles`, à côté.
- **La provenance d'archive se capte au téléchargement** (`dila.provenance`) : la licence
  impose de citer l'URL longue, le nom du fichier et sa date. Une archive locale ne
  renseigne rien plutôt qu'une URL reconstruite.
- Le vocabulaire d'alertes est un **contrat** : ajouter un code est additif, en retirer
  un est breaking (le service en publie l'`enum` dans son OpenAPI).
- Imports lourds (`duckdb`, `defusedxml`) **dans** les fonctions, jamais au module.

## Gotchas

- **Le parquet des bilans INPI est un dataset EXTERNE** (Signaux Faibles, via data.gouv),
  stocké en **INT32** : tout montant > 2 147 483 647 sature et se lit comme *absent*
  (5,2 % des dépôts consolidés ; la liasse est amputée de ses plus gros postes, donc tout
  ratio calculé dessus est faux sans le dire — `#10`). On ne le rebuilde pas : la valeur
  est détruite en amont, seul le **fait** du débordement est détectable.
- **Bumper la version à chaque publication** : PyPI refuse de réécrire une version.
- Un `publish` PyPI **n'atteint pas** les installations editable d'une machine — et la
  propagation d'index prend plusieurs minutes sur certains points de sortie, ce qui fait
  échouer un déploiement consommateur lancé dans la foulée.

## Key Concepts

- **La licence est PROPRE au fonds, le producteur aussi.** Les fonds DILA partagent la
  licence ouverte v2.0 et ses trois mentions (`dila`), mais pas leur producteur — les
  accords viennent du ministère du Travail, les conventions collectives de la DILA. Les
  confondre est déjà une attribution fausse.
- **Vérifier une licence, c'est partir de ce qu'on télécharge** — l'URL, l'identifiant de
  ressource — puis remonter au jeu de données. Une recherche par mots-clés rend des jeux
  réels et faux.

## Docs

- `docs/catalogue.md` — inventaire « grosse maille » des sources open data FR branchées
  ou candidates (source · ce que ça donne · clé · exposition côté oto · statut). Le
  détail d'un client vit dans son module, pas ici.
