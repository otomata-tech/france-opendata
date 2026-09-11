"""IREP — registre des émissions polluantes déclarées, par ÉTABLISSEMENT (open data).

Source : Géorisques / ministère de la Transition écologique,
  https://files.georisques.fr/irep/<annee>.zip
Sans clé, Licence Ouverte. Un ZIP par année, deux fichiers utiles :
`etablissements.csv` (~3,5 Mo — identité, SIRET, commune, coordonnées WGS84) et
`emissions.csv` (~9,7 Mo — une ligne par établissement × polluant × milieu).

**Ce que ça apporte face à BEGES.** Un bilan GES porte sur une ORGANISATION entière,
tous sites confondus : il dit qu'un groupe émet, jamais où. L'IREP déclare par
ÉTABLISSEMENT, avec son SIRET et ses coordonnées — c'est ce qui permet de désigner
*quel* site d'un grand compte pèse, et donc à quelle adresse se présenter.

⚠️ **89 % des quantités ne sont pas des nombres.** Sur le millésime 2024, 56 848
lignes sur 64 045 portent la chaîne « < seuil » : l'exploitant a déclaré une émission
inférieure au seuil de déclaration. Ce n'est ni zéro (il émet) ni une absence (il a
déclaré). `float()` y plante, et y mettre 0 invente une mesure. Les quantités sortent
donc en `(quantite, sous_seuil)` : un nombre, ou `None` avec `sous_seuil=True`.

⚠️ **Les CSV sont en UTF-8**, et c'est un piège à l'envers : ouverts en latin-1 — le
réflexe sur un jeu administratif français — ils rendent « Base Aérienne » sous la forme
« Base AÃ©rienne ». Le mojibake ne vient pas du fichier mais de sa lecture, et rien ne
le signale : les noms restent des chaînes valides, simplement fausses. Vérifié le
11/09/2026 sur l'établissement `0009000027`.

⚠️ **Le CO2 existe en TROIS variantes** dans le jeu : « d'origine non biomasse » (le
CO2 fossile, celui qui compte pour l'empreinte), « d'origine biomasse », et le
« total » qui somme les deux. Les confondre gonfle le chiffre d'un site qui brûle du
bois. Et une recherche sur « carbone » attrape en plus le monoxyde de carbone, qui
n'est pas un gaz à effet de serre au même titre.

⚠️ **`code_departement` porte un espace de fin** dans le jeu source (« 01 ») : les
comparaisons se font sur la valeur nettoyée.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from typing import Any, Optional

import requests

from ._http import DEFAULT_TIMEOUT

URL = "https://files.georisques.fr/irep/{annee}.zip"
ENCODAGE = "utf-8"

CO2_FOSSILE = "Dioxyde de carbone (CO2) d'origine non biomasse"
CO2_TOTAL = "Dioxyde de carbone (CO2) total (d'origine biomasse et non biomasse)"

# Le registre couvre l'air, l'eau et le sol : sans filtre, un rejet aqueux se
# retrouverait classé à côté d'une émission atmosphérique.
MILIEU_AIR = "Air"


def _texte(valeur: Any) -> Optional[str]:
    if valeur is None:
        return None
    t = str(valeur).strip()
    return t or None


def quantite_declaree(brut: Any) -> tuple[Optional[float], bool]:
    """Rend `(quantite, sous_seuil)` — jamais un zéro inventé.

    « < seuil » signifie que l'exploitant a déclaré une émission INFÉRIEURE au seuil
    de déclaration : il émet, mais sous la borne. Rendre 0 effacerait le fait de la
    déclaration ; rendre None sans le dire le confondrait avec un non-déclarant.
    """
    t = _texte(brut)
    if t is None:
        return None, False
    if t.startswith("<"):
        return None, True
    try:
        return float(t.replace(",", ".")), False
    except ValueError:
        return None, False


def libelle_inconnu(champ: str, valeur: str, connus: set[str], annee: int) -> str:
    """Le message d'un libellé absent du millésime, avec les libellés qui s'en approchent.

    Filtré en silence, un libellé approximatif (« CO2 Total ») rendrait une liste
    vide — indiscernable de « aucun émetteur ». Il est refusé, et le refus dit quoi
    écrire à la place. Un mot rare pèse plus qu'un mot courant : dans « CO2 Total »,
    « CO2 » désigne trois libellés, « total » une vingtaine.
    """
    def plier(s: str) -> str:  # « methane » doit trouver « Méthane (CH4) »
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()

    bas = {c: plier(c) for c in connus}
    mots = {m for m in re.findall(r"\w+", plier(valeur)) if len(m) > 1}
    poids = {m: 1 / n for m in mots if (n := sum(m in b for b in bas.values()))}
    notes = sorted(((sum(p for m, p in poids.items() if m in bas[c]), c) for c in connus),
                   key=lambda x: (-x[0], len(x[1])))
    proches = [c for n, c in notes[:3] if n > 0]
    msg = f"{champ} inconnu du registre {annee} : {valeur!r} — libellé exact attendu"
    return msg + (f" ; proches : {' | '.join(proches)}" if proches else "")


def _etablissement(row: dict[str, str]) -> dict[str, Any]:
    return {
        "identifiant": _texte(row.get("identifiant")),
        "nom": _texte(row.get("nom_etablissement")),
        "siret": _texte(row.get("numero_siret")),
        "adresse": _texte(row.get("adresse")),
        "code_postal": _texte(row.get("code_postal")),
        "code_commune": _texte(row.get("code_insee")),
        "commune": _texte(row.get("commune")),
        "code_departement": _texte(row.get("code_departement")),
        "naf": _texte(row.get("code_ape")),
        "libelle_naf": _texte(row.get("libelle_ape")),
        "latitude": _texte(row.get("coordonnees_y")),
        "longitude": _texte(row.get("coordonnees_x")),
    }


class IrepClient:
    """Émissions déclarées par établissement. Sans clé.

    Le millésime est un ZIP de ~7,5 Mo : il est téléchargé une fois puis gardé en
    mémoire par instance, comme le stock FINESS.
    """

    def __init__(self, timeout: Any = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.session = requests.Session()
        self._cache: dict[int, tuple[dict[str, dict], list[dict], dict[str, set[str]]]] = {}

    def _lire_csv(self, z: zipfile.ZipFile, nom: str) -> list[dict[str, str]]:
        with z.open(nom) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, encoding=ENCODAGE), delimiter=";"))

    def _charger(self, annee: int) -> tuple[dict[str, dict], list[dict], dict[str, set[str]]]:
        if annee in self._cache:
            return self._cache[annee]
        resp = self.session.get(
            URL.format(annee=annee),
            headers={"User-Agent": "france-opendata"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        etabs = {
            e["identifiant"]: e
            for e in map(_etablissement, self._lire_csv(z, f"{annee}/etablissements.csv"))
            if e["identifiant"]
        }
        emissions = self._lire_csv(z, f"{annee}/emissions.csv")
        libelles = {champ: {t for r in emissions if (t := _texte(r.get(champ)))}
                    for champ in ("polluant", "milieu")}
        self._cache[annee] = (etabs, emissions, libelles)
        return self._cache[annee]

    def emetteurs(
        self,
        annee: int = 2024,
        departement: Optional[str] = None,
        code_commune: Optional[str] = None,
        siret: Optional[str] = None,
        polluant: Optional[str] = CO2_FOSSILE,
        milieu: Optional[str] = MILIEU_AIR,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Établissements déclarants, du plus gros émetteur au plus petit.

        Args:
            annee: millésime du registre (le ZIP publié pour cette année).
            departement: code département INSEE.
            code_commune: code INSEE de la commune.
            siret: un établissement précis.
            polluant: libellé EXACT du jeu. Par défaut le CO2 fossile
                (`CO2_FOSSILE`) ; `None` ne filtre pas.
            milieu: « Air » par défaut ; `None` prend aussi l'eau et le sol.
            limit: établissements rendus.

        Raises:
            ValueError: `polluant` ou `milieu` absent du millésime — le message
                propose les libellés proches.

        Returns:
            `{"annee", "total", "tronque", "sous_seuil", "signaux": [...]}`.
            `sous_seuil` compte les déclarations retenues dont la quantité est sous
            le seuil : elles sont RENDUES, en fin de liste, avec `quantite: None`.
        """
        etabs, emissions, libelles = self._charger(annee)
        for champ, valeur in (("polluant", polluant), ("milieu", milieu)):
            if valeur and valeur not in libelles[champ]:
                raise ValueError(libelle_inconnu(champ, valeur, libelles[champ], annee))
        dep = (departement or "").strip()
        retenues = []
        for row in emissions:
            if milieu and _texte(row.get("milieu")) != milieu:
                continue
            if polluant and _texte(row.get("polluant")) != polluant:
                continue
            if dep and (_texte(row.get("code_departement")) or "") != dep:
                continue
            if code_commune and _texte(row.get("code_insee")) != code_commune:
                continue
            ident = _texte(row.get("identifiant"))
            etab = etabs.get(ident or "", {})
            if siret and etab.get("siret") != siret:
                continue
            q, sous_seuil = quantite_declaree(row.get("quantite"))
            retenues.append({
                "ref_key": f"{ident}|{annee}|{_texte(row.get('polluant'))}",
                **etab,
                "polluant": _texte(row.get("polluant")),
                "milieu": _texte(row.get("milieu")),
                "annee": _texte(row.get("annee_emission")),
                "quantite": q,
                "unite": _texte(row.get("unite")),
                "sous_seuil": sous_seuil,
            })

        # Les quantités connues d'abord, décroissantes ; les « < seuil » ensuite —
        # elles sont une information, mais pas un classement.
        retenues.sort(key=lambda s: (s["quantite"] is None, -(s["quantite"] or 0)))
        borne = max(1, int(limit))
        return {
            "annee": annee,
            "total": min(len(retenues), borne),
            "tronque": len(retenues) > borne,
            "sous_seuil": sum(1 for s in retenues[:borne] if s["sous_seuil"]),
            "signaux": retenues[:borne],
        }
