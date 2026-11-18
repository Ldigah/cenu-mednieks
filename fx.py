"""Free currency conversion to EUR (frankfurter.app — ECB rates, no API key)."""
from __future__ import annotations

import functools

import requests


@functools.lru_cache(maxsize=8)
def rate_to_eur(currency: str) -> float | None:
    """Exchange rate currency → EUR (1.0 for EUR); None if unreachable."""
    if not currency or currency == "EUR":
        return 1.0
    try:
        r = requests.get("https://api.frankfurter.app/latest",
                         params={"from": currency.upper(), "to": "EUR"}, timeout=10)
        r.raise_for_status()
        return float(r.json()["rates"]["EUR"])
    except (requests.RequestException, KeyError, ValueError, TypeError):
        return None
