"""Réglages HTTP partagés des connecteurs.

`DEFAULT_TIMEOUT` est un couple `(connect, read)` en secondes : un timeout de
**connexion court** (fail-fast) est crucial — sans lui, un host injoignable laisse
l'appel `requests` pendre jusqu'au timeout TCP de l'OS (~2 min), ce qui peut geler
un appelant mono-thread (event loop asyncio). Le timeout de lecture est plus large
pour absorber les réponses lentes des portails open data.
"""
from __future__ import annotations

DEFAULT_TIMEOUT: tuple[float, float] = (5, 30)  # (connect, read) secondes
