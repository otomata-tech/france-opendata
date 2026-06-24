"""DVF — Demandes de Valeurs Foncières (transactions immobilières, open data).

Source : **API Données foncières du Cerema/DGALN** (flux DVF+ open-data, ouvert,
sans clé), modèle DVF+ agrégé à la mutation.
  https://apidf-preprod.cerema.fr/dvf_opendata/{mutations,geomutations}/
Pas de clé. Licence Ouverte. Profondeur : **2014 → courante**.

Principe : **on expose la donnée brute, on ne filtre pas à la place de l'agent.**
Une mutation = une ligne, TOUS types de biens (logement, dépendance, terrain,
local d'activité, bâti mixte…), TOUTES natures (vente, adjudication, échange…).
On AJOUTE des champs dérivés pratiques (prix_m2 quand calculable, type_local
résidentiel lisible, centroïde lon/lat, adresse reverse-géocodée) sans jamais
retirer de ligne. Les filtres (`type_local`, `surface_min/max`) sont **optionnels**
— absents = tout passe. L'agent compose l'usage : valorisation €/m², analyse
foncière, volume de marché, détection VEFA, etc.

`stats()` est l'exception assumée : c'est un agrégat de **valorisation** qui, lui,
restreint au résidentiel mono-bien (codtypbien 111/121) et écarte les outliers,
sinon la médiane €/m² n'a pas de sens. La liste brute, elle, ne filtre rien.

Localisation : DVF+ géolocalise à la **parcelle** (MultiPolygon), pas en point. On
calcule un centroïde (lon/lat) et, pour le filtre par adresse, la distance au plus
proche sommet de la parcelle (robuste aux biens multi-parcelles). L'adresse texte
est reconstituée par reverse-géocodage **BAN en batch** (DVF+ ne la porte pas).
"""
from __future__ import annotations

import csv
import io
import math
import statistics
from datetime import datetime
from typing import Any, Optional

import requests


API_BASE = "https://apidf-preprod.cerema.fr/dvf_opendata"
BAN_URL = "https://api-adresse.data.gouv.fr/search/"
BAN_REVERSE_CSV = "https://api-adresse.data.gouv.fr/reverse/csv/"
FIRST_YEAR = 2014
PAGE_SIZE = 500
MAX_PAGES = 2000  # garde-fou pagination (PAGE_SIZE * MAX_PAGES = 1M mutations)

# codtypbien DVF+ du résidentiel mono-bien → libellé normalisé (sert au filtre
# optionnel `type_local` et au calcul de `stats`). Tout autre code passe quand même
# dans la liste brute (avec son `type_bien` DVF+ d'origine), juste `type_local=None`.
CODTYPBIEN_TO_TYPE = {"111": "Maison", "121": "Appartement"}
TYPE_TO_CODTYPBIEN = {"Maison": "111", "Appartement": "121"}

# Bornes outliers — appliquées UNIQUEMENT par stats() (médiane €/m² de valorisation),
# jamais à la liste brute.
PRIX_M2_MIN = 100
PRIX_M2_MAX = 50000


class DvfClient:
    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self.session = requests.Session()

    # ---- accès API DVF+ -----------------------------------------------------

    def _paginate(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Pagine un endpoint DVF+ (mutations|geomutations) et concatène les items.
        `mutations` renvoie {count,next,results}, `geomutations` un GeoJSON
        {count,next,features} — on gère les deux."""
        url = f"{API_BASE}/{endpoint}/"
        q: Optional[dict[str, Any]] = {**params, "page_size": PAGE_SIZE}
        items: list[dict[str, Any]] = []
        pages = 0
        while url and pages < MAX_PAGES:
            r = self.session.get(url, params=q, timeout=self.timeout)
            r.raise_for_status()
            d = r.json()
            items.extend(d.get("results") or d.get("features") or [])
            url = d.get("next")  # URL absolue, params déjà inclus
            q = None
            pages += 1
        return items

    def _fetch_window(
        self, endpoint: str, key_params: dict[str, Any], years: int
    ) -> list[dict[str, Any]]:
        """Récupère sur les `years` dernières années AVEC data, en filtrant
        `anneemut` **côté serveur** année par année (DVF+ a ~6 mois de lag, donc
        l'année courante est souvent vide — on l'ignore et on remonte). Bien plus
        léger que paginer tout l'historique puis filtrer."""
        current = datetime.now().year
        items: list[dict[str, Any]] = []
        collected = 0
        y = current
        while y >= FIRST_YEAR and collected < years:
            page = self._paginate(endpoint, {**key_params, "anneemut": y})
            if page:
                items.extend(page)
                collected += 1
            y -= 1
        return items

    def geocode(self, adresse: str) -> Optional[dict[str, Any]]:
        """Géocode une adresse via la BAN (Base Adresse Nationale, keyless).

        Renvoie {lon, lat, code_commune, label, score} ou None si pas de match.
        """
        r = self.session.get(BAN_URL, params={"q": adresse, "limit": 1}, timeout=self.timeout)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if not feats:
            return None
        f = feats[0]
        lon, lat = f["geometry"]["coordinates"]
        p = f["properties"]
        return {
            "lon": lon,
            "lat": lat,
            "code_commune": p.get("citycode"),
            "label": p.get("label"),
            "score": p.get("score"),
        }

    def _reverse_addresses(self, rows: list[dict[str, Any]]) -> None:
        """Reconstitue l'adresse postale de chaque ligne par reverse-géocodage BAN
        **en un seul appel batch** (CSV). Mute `rows` en place : ajoute adresse /
        code_postal / commune. Sans coordonnées = champs None (jamais inventés)."""
        located = [c for c in rows if c.get("latitude") and c.get("longitude")]
        if not located:
            return
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["lat", "lon"])
        for c in located:
            w.writerow([c["latitude"], c["longitude"]])
        r = self.session.post(
            BAN_REVERSE_CSV,
            files={"data": ("points.csv", buf.getvalue())},
            timeout=self.timeout,
        )
        r.raise_for_status()
        out = list(csv.DictReader(io.StringIO(r.text)))
        for c, row in zip(located, out):
            c["adresse"] = row.get("result_label") or None
            c["code_postal"] = row.get("result_postcode") or None
            c["commune"] = row.get("result_city") or None

    # ---- normalisation (SANS filtrage de contenu) ---------------------------

    def _normalize(
        self,
        items: list[dict[str, Any]],
        type_local: Optional[str],
        surface_min: Optional[float],
        surface_max: Optional[float],
    ) -> list[dict[str, Any]]:
        """Mappe les mutations DVF+ vers un schéma riche, **sans retirer de ligne**.
        Seuls les filtres EXPLICITES (`type_local`, `surface_min/max`), s'ils sont
        fournis, restreignent — sinon tout passe. `prix_m2` vaut None quand non
        calculable (pas de surface bâtie), ce n'est pas un motif d'exclusion."""
        out: list[dict[str, Any]] = []
        for it in items:
            # geomutations → {properties, geometry} ; mutations → dict plat.
            props = it.get("properties", it)
            cod = props.get("codtypbien")
            tl = CODTYPBIEN_TO_TYPE.get(cod)  # None si non-résidentiel-mono

            if type_local and tl != type_local:
                continue

            sbati = _to_float(props.get("sbati"))
            valeur = _to_float(props.get("valeurfonc"))
            if surface_min is not None and (sbati is None or sbati < surface_min):
                continue
            if surface_max is not None and (sbati is None or sbati > surface_max):
                continue

            prix_m2 = round(valeur / sbati) if (valeur and sbati and sbati > 0) else None
            lon, lat = _centroid(it.get("geometry"))
            parcelles = props.get("l_idpar") or []
            out.append({
                "id_mutation": props.get("idopendata"),
                "date_mutation": props.get("datemut"),
                "annee": props.get("anneemut"),
                "nature_mutation": props.get("libnatmut"),
                "valeur_fonciere": round(valeur) if valeur is not None else None,
                "type_local": tl,                      # résidentiel mono (sinon None)
                "type_bien": props.get("libtypbien"),  # libellé DVF+ brut, tous types
                "codtypbien": cod,
                "surface_reelle_bati": round(sbati) if sbati is not None else None,
                "surface_terrain": round(_to_float(props.get("sterr")) or 0) or None,
                "nombre_locaux": props.get("nblocmut"),
                "nombre_parcelles": props.get("nbpar"),
                "prix_m2": prix_m2,
                "vefa": props.get("vefa"),
                "id_parcelle": parcelles[0] if parcelles else None,
                "id_parcelles": parcelles,
                "_geometry": it.get("geometry"),  # interne (distance), retiré avant sortie
                "longitude": lon,
                "latitude": lat,
            })
        return out

    # ---- API publique -------------------------------------------------------

    def comparables(
        self,
        code_commune: str,
        type_local: Optional[str] = None,
        surface_min: Optional[float] = None,
        surface_max: Optional[float] = None,
        years: int = 2,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Mutations DVF+ d'une commune, **brutes** (tous types de biens et de
        natures), plus récentes d'abord. Aucun filtrage implicite : la liste sert
        autant la valorisation que l'analyse foncière / le volume de marché.

        Chaque ligne : date, nature_mutation, valeur_fonciere, type_bien (libellé
        DVF+) + type_local (résidentiel mono, sinon None), surface_reelle_bati,
        surface_terrain, prix_m2 (None si non calculable), nombre_locaux, vefa,
        id_parcelle(s), adresse (reverse BAN), lon/lat.

        Args:
            code_commune: code INSEE 5 chiffres (ex. "13201").
            type_local: filtre OPTIONNEL "Appartement" | "Maison" (défaut : tout).
            surface_min/max: filtres OPTIONNELS sur la surface bâtie m².
            years: profondeur en années avec data (défaut 2, jusqu'à ~2014).
            limit: nb max de lignes retournées (les plus récentes).
        """
        feats = self._fetch_window("geomutations", {"code_insee": code_commune}, years)
        rows = self._normalize(feats, type_local, surface_min, surface_max)
        rows.sort(key=lambda c: c["date_mutation"] or "", reverse=True)
        rows = rows[:limit]
        self._reverse_addresses(rows)
        for c in rows:
            c.pop("_geometry", None)
        return {
            "code_commune": code_commune,
            "type_local": type_local,
            "years": years,
            "count": len(rows),
            "mutations": rows,
        }

    def comparables_by_address(
        self,
        adresse: str,
        radius_m: int = 500,
        type_local: Optional[str] = None,
        surface_min: Optional[float] = None,
        surface_max: Optional[float] = None,
        years: int = 3,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Mutations DVF+ **brutes** autour d'une adresse (géocode BAN + emprise +
        filtre rayon), plus proches d'abord. Le rayon est mesuré au plus proche
        sommet de parcelle (robuste aux biens multi-parcelles). Mêmes champs que
        `comparables`, plus `distance_m`. `median_prix_m2` est calculé sur les
        seules lignes résidentielles mono-bien (indicatif).

        Args:
            adresse: adresse libre (ex. "44 la canebière marseille").
            radius_m: rayon de recherche en mètres autour du point géocodé.
            type_local / surface_min / surface_max / years / limit : cf. comparables().
        """
        geo = self.geocode(adresse)
        if not geo:
            return {"adresse": adresse, "error": "geocode_failed", "mutations": [], "count": 0}
        lon, lat = geo["lon"], geo["lat"]
        dlat = radius_m / 111_320
        dlon = radius_m / (111_320 * max(math.cos(math.radians(lat)), 1e-6))
        bbox = f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}"

        feats = self._paginate("geomutations", {"in_bbox": bbox})
        # filtre années côté client (l'emprise borne déjà le volume) : les `years`
        # dernières années AVEC data dans la zone (basé sur le millésime max présent).
        anns = [f.get("properties", {}).get("anneemut") for f in feats]
        anns = [a for a in anns if a]
        if anns:
            cutoff = max(anns) - years + 1
            feats = [f for f in feats if (f.get("properties", {}).get("anneemut") or 0) >= cutoff]
        rows = self._normalize(feats, type_local, surface_min, surface_max)
        near: list[dict[str, Any]] = []
        for c in rows:
            dist = _min_distance_m(lat, lon, c.get("_geometry"))
            if dist is not None and dist <= radius_m:
                c["distance_m"] = round(dist)
                near.append(c)
        near.sort(key=lambda c: c["distance_m"])
        near = near[:limit]
        self._reverse_addresses(near)
        for c in near:
            c.pop("_geometry", None)

        res_pm2 = [c["prix_m2"] for c in near if c["prix_m2"] and c["type_local"]]
        return {
            "adresse_geocodee": geo["label"],
            "code_commune": geo["code_commune"],
            "radius_m": radius_m,
            "type_local": type_local,
            "years": years,
            "count": len(near),
            "median_prix_m2": round(statistics.median(res_pm2)) if res_pm2 else None,
            "mutations": near,
        }

    def stats(
        self,
        code_commune: str,
        type_local: Optional[str] = None,
        years: int = 3,
    ) -> dict[str, Any]:
        """Agrégat de **valorisation** : médiane/moyenne/min/max €/m² + ventilation
        annuelle. Contrairement aux listes brutes, restreint au **résidentiel
        mono-bien** (codtypbien 111/121) et écarte les outliers (€/m² <100 ou
        >50000) — sinon la médiane n'a pas de sens. Pour la donnée non filtrée,
        utiliser `comparables`.

        Args:
            code_commune: code INSEE 5 chiffres.
            type_local: "Appartement" | "Maison" (défaut : les deux résidentiels).
            years: profondeur (défaut 3, jusqu'à ~2014).
        """
        items = self._fetch_window("mutations", {"code_insee": code_commune}, years)
        rows = self._normalize(items, type_local, None, None)
        # valorisation = résidentiel mono-bien avec €/m² dans les bornes
        clean = [c for c in rows
                 if c["type_local"] and c["prix_m2"]
                 and PRIX_M2_MIN <= c["prix_m2"] <= PRIX_M2_MAX]
        prix_m2 = [c["prix_m2"] for c in clean]

        by_year: dict[str, dict[str, Any]] = {}
        for c in clean:
            yr = str(c["annee"])
            d = by_year.setdefault(yr, {"count": 0, "_pm2": []})
            d["count"] += 1
            d["_pm2"].append(c["prix_m2"])
        for yr, d in by_year.items():
            pm2 = d.pop("_pm2")
            d["median_prix_m2"] = round(statistics.median(pm2)) if pm2 else None

        return {
            "code_commune": code_commune,
            "type_local": type_local,
            "years": years,
            "count": len(clean),
            "median_prix_m2": round(statistics.median(prix_m2)) if prix_m2 else None,
            "mean_prix_m2": round(statistics.mean(prix_m2)) if prix_m2 else None,
            "min_prix_m2": round(min(prix_m2)) if prix_m2 else None,
            "max_prix_m2": round(max(prix_m2)) if prix_m2 else None,
            "by_year": dict(sorted(by_year.items())),
        }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance en mètres entre 2 points (lat/lon degrés)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _exterior_points(geom: Optional[dict[str, Any]]) -> list[list[float]]:
    """Sommets des anneaux extérieurs d'une géométrie DVF+ (Polygon|MultiPolygon)."""
    if not geom:
        return []
    coords = geom.get("coordinates") or []
    rings: list[list[Any]] = []
    if geom.get("type") == "MultiPolygon":
        rings = [poly[0] for poly in coords if poly]
    elif geom.get("type") == "Polygon":
        rings = [coords[0]] if coords else []
    return [pt for ring in rings for pt in ring]


def _centroid(geom: Optional[dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    """Centroïde (lon, lat) approché : moyenne des sommets extérieurs. Sert à
    l'affichage et au reverse-géocodage. (None, None) si pas de géométrie."""
    pts = _exterior_points(geom)
    if not pts:
        return (None, None)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (round(sum(xs) / len(xs), 6), round(sum(ys) / len(ys), 6))


def _min_distance_m(lat: float, lon: float, geom: Optional[dict[str, Any]]) -> Optional[float]:
    """Distance (m) du point au plus proche sommet de la parcelle. Robuste aux
    biens multi-parcelles (le centroïde global, lui, peut tomber hors rayon)."""
    pts = _exterior_points(geom)
    if not pts:
        return None
    return min(_haversine_m(lat, lon, p[1], p[0]) for p in pts)


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
