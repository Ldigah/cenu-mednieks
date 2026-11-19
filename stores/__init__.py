"""Store adapters: domain → extraction strategy.

Registry lives here; one module per store (stores/salidzini.py, stores/kurpirkt.py,
stores/ceno.py, stores/presets.py). Presets verified against live pages on 2026-11-18
by the selector-finder research. The "*" adapter auto-handles any other shop via
JSON-LD. Extraction: JSON-LD first, CSS fallback, then optional price_regex over
inline scripts. Non-EUR prices convert to EUR (fx.py).
"""
from __future__ import annotations

from urllib.parse import urlparse

import stores.ceno as _ceno
import stores.kurpirkt as _kurpirkt
import stores.presets as _presets
import stores.salidzini as _salidzini
from stores.base import EXCLUDE_CATEGORY_RE, StoreAdapter, make_auto_adapter

__all__ = ["EXCLUDE_CATEGORY_RE", "StoreAdapter", "STORES", "adapter_for",
           "make_auto_adapter", "register", "supported_store_list"]

STORES: dict[str, StoreAdapter] = {}


def register(adapter: StoreAdapter):
    STORES[adapter.domain] = adapter


for _preset in (_salidzini.SALIDZINI, _presets.AMAZON_DE, _presets.AMAZON_PL,
                _kurpirkt.KURPIKT, _ceno.CENO, _presets.PIGU,
                _presets.HINNAVAATLUS, _presets.AUTO):
    register(_preset)


def adapter_for(url: str) -> StoreAdapter:
    """Adapter for a URL; unknown domains get a generic JSON-LD "auto" adapter."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host in STORES:
        return STORES[host]
    for domain, adapter in STORES.items():
        if host.endswith("." + domain):
            return adapter
    if not host:
        return STORES["*"]
    return make_auto_adapter(host)


def supported_store_list() -> str:
    known = [a.label for d, a in sorted(STORES.items()) if d != "*"]
    return ", ".join(known) + " — plus any other shop with JSON-LD product data"
