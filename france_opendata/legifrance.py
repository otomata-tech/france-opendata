"""Légifrance — API PISTE (DILA) : consultation à la demande des fonds exposés.

Premier usage : le **texte intégral des accords d'entreprise** (fonds ACCO).
L'ingestion locale (`acco_ingest`) n'indexe que les métadonnées — le texte vit
dans des .docx que Légifrance sert à la demande : `POST /consult/acco {"id"}`
renvoie les métadonnées + `data` = le **docx en base64**. `acco_text(id)` le
décode et extrait le texte brut (zip + word/document.xml, sans dépendance).

Auth OAuth2 PISTE client_credentials (`PISTE_CLIENT_ID` / `PISTE_CLIENT_SECRET`,
env ou constructeur) — même app que Judilibre, souscription « Légifrance ».
"""
from __future__ import annotations

import base64
import io
import os
import re
import time
import zipfile
from typing import Any, Optional

import requests

OAUTH_URL = "https://oauth.piste.gouv.fr/api/oauth/token"
BASE_URL = "https://api.piste.gouv.fr/dila/legifrance/lf-engine-app"

_WP_RE = re.compile(r"</w:p>")
_TAG_RE = re.compile(r"<[^>]+>")


def _ms_to_date(ms) -> Optional[str]:
    """Les dates Légifrance arrivent en epoch millisecondes → YYYY-MM-DD."""
    if not ms:
        return None
    import datetime
    return datetime.datetime.fromtimestamp(int(ms) / 1000, tz=datetime.timezone.utc).date().isoformat()


def docx_to_text(docx_bytes: bytes) -> str:
    """Texte brut d'un .docx : word/document.xml, fins de paragraphes préservées."""
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    text = _WP_RE.sub("\n", xml)
    text = _TAG_RE.sub("", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class LegifranceClient:
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.client_id = client_id or os.environ.get("PISTE_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("PISTE_CLIENT_SECRET")
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "credentials PISTE absents (PISTE_CLIENT_ID / PISTE_CLIENT_SECRET) — "
                "app piste.gouv.fr avec souscription Légifrance requise")
        self._sess = requests.Session()
        self._sess.headers["User-Agent"] = "france-opendata/legifrance (+https://github.com/otomata-tech/france-opendata)"
        self._token_at = 0.0

    def _auth(self) -> None:
        if time.time() - self._token_at < 3000:  # tokens PISTE ~1h
            return
        r = requests.post(OAUTH_URL, data={
            "grant_type": "client_credentials", "client_id": self.client_id,
            "client_secret": self.client_secret, "scope": "openid"}, timeout=20)
        r.raise_for_status()
        self._sess.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        self._token_at = time.time()

    def acco_text(self, acco_id: str) -> Optional[dict[str, Any]]:
        """Texte intégral + métadonnées d'un accord d'entreprise (ACCOTEXT…).

        Renvoie None si l'id est inconnu. `texte` peut être vide si l'accord
        n'a pas de version intégrale publiée (conformeVersionIntegrale=false
        ne l'exclut pas toujours — on renvoie ce que Légifrance sert)."""
        self._auth()
        r = self._sess.post(f"{BASE_URL}/consult/acco",
                            json={"id": acco_id}, timeout=60)
        if r.status_code in (400, 404):  # id inconnu → 400 chez Légifrance
            return None
        r.raise_for_status()
        acco = r.json().get("acco") or {}
        if not acco.get("id"):
            return None
        texte = ""
        if acco.get("data"):
            try:
                texte = docx_to_text(base64.b64decode(acco["data"]))
            except Exception:  # noqa: BLE001 — docx corrompu → texte vide, méta servies
                texte = ""
        return {
            "id": acco["id"],
            "titre": acco.get("titreTexte"),
            "nature": acco.get("nature"),
            "numero": acco.get("numero"),
            "siret": acco.get("siret"),
            "raison_sociale": acco.get("raisonSociale"),
            "code_idcc": acco.get("codeIdcc"),
            "date_texte": _ms_to_date(acco.get("dateTexte")),
            "date_effet": _ms_to_date(acco.get("dateEffet")),
            "conforme_version_integrale": acco.get("conformeVersionIntegrale"),
            "syndicats": [s.get("libelle") for s in (acco.get("syndicats") or [])
                          if isinstance(s, dict) and s.get("libelle")],
            "texte": texte,
        }
