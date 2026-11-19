"""Product-centric catalog: one product, many store offers.

v2 schema (products.json):
  {"version": 2, "products": [{
      "id": "jbl-flip-7",
      "name": "JBL Flip 7",
      "threshold_pct": 5.0,          # null → DEFAULT_DROP_PCT
      "added": "…iso…",
      "atl": {"price": 71.9, "date": "…iso…", "url": "…", "store": "24.lv"},
      "last_min": {"price": 73.0, "url": "…", "store": "24.lv"},
      "offers": [{"url": "…", "store": "salidzini.lv", "label": "salidzini.lv"}]
  }]}

prices.json stays keyed by offer URL (unchanged shape), so merge_prices is untouched.
Migration from v1 (flat URL entries) groups entries by product name — pure, tested.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import urlparse

import parse
import stores
from commands import slugify

CATALOG_VERSION = 2

# Aggregators whose /search?q= endpoint IS an offers list — used by /add discovery.
DISCOVERY_STORES = ("salidzini.lv", "kurpirkt.lv", "ceno.lv")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _normalize_offer(offer: dict) -> dict:
    url = (offer.get("url") or "").strip()
    host = offer.get("store") or _host_of(url)
    return {"url": url, "store": host,
            "label": offer.get("label") or host}


def _normalize_product(prod: dict) -> dict:
    offers = [_normalize_offer(o) for o in prod.get("offers") or []]
    # de-duplicate by URL, keep first
    seen: set[str] = set()
    uniq = []
    for o in offers:
        if o["url"] and o["url"] not in seen:
            seen.add(o["url"])
            uniq.append(o)
    return {
        "id": prod.get("id") or slugify(prod.get("name") or "item"),
        "name": (prod.get("name") or "").strip(),
        "threshold_pct": prod.get("threshold_pct"),
        "added": prod.get("added") or _now_iso(),
        "atl": prod.get("atl"),          # {"price","date","url","store"} | None
        "last_min": prod.get("last_min"),  # {"price","url","store"} | None
        "offers": uniq,
    }


def migrate_products(raw: dict | None) -> dict:
    """Accept v1 (flat url-entries) or v2; always return the v2 shape."""
    raw = raw or {}
    if raw.get("version") == CATALOG_VERSION:
        prods = [_normalize_product(p) for p in raw.get("products") or []]
        return {"version": CATALOG_VERSION, "products": prods}
    # v1: group flat entries by product name (empty name → keep old id as the group key)
    grouped: dict[str, dict] = {}
    for e in raw.get("products") or []:
        name = (e.get("name") or "").strip()
        key = slugify(name) if name else e.get("id") or "item"
        prod = grouped.get(key)
        if prod is None:
            prod = {"id": key, "name": name or key, "threshold_pct": None,
                    "added": e.get("added") or _now_iso(), "atl": None,
                    "last_min": None, "offers": []}
            grouped[key] = prod
        host = _host_of(e.get("url") or "")
        prod["offers"].append(_normalize_offer(
            {"url": e.get("url"), "store": host, "label": e.get("store_label") or host}))
    return {"version": CATALOG_VERSION, "products": list(grouped.values())}


def looks_like_url(token: str) -> bool:
    """/add heuristics: a URL has a scheme, or a dot-domain with no spaces."""
    t = (token or "").strip()
    if not t or " " in t:
        return False
    if t.lower().startswith(("http://", "https://")):
        return True
    host = _host_of("https://" + t)
    return bool(host) and "." in host


def make_offer(url: str) -> tuple[dict | None, str]:
    """Build an offer from a URL → (offer, note) or (None, error)."""
    u = (url or "").strip()
    if u and not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    host = _host_of(u)
    if not host or "." not in host:
        return None, f"That doesn't look like a product URL: {url}"
    adapter = stores.adapter_for(u)
    note = ""
    if adapter.domain not in stores.STORES:
        note = ("\n⚠️ Unknown shop — I'll try JSON-LD auto-detection; "
                "if it fails 3× a store preset is needed.")
    return _normalize_offer({"url": u, "store": host, "label": host}), note


def make_product(query: str, offers: list[dict] | None = None) -> dict:
    return _normalize_product({"id": slugify(query), "name": query.strip(),
                               "threshold_pct": None, "offers": offers or []})


def discovery_offers(query: str) -> list[dict]:
    """Aggregator search URLs for a query — the offers get scraped on this very run."""
    import urllib.parse
    q = urllib.parse.quote_plus(query)
    urls = [
        f"https://www.salidzini.lv/search?q={q}",
        f"https://www.kurpirkt.lv/cena.php?q={q}",
        f"https://ceno.lv/search?q={q}",
    ]
    return [_normalize_offer({"url": u, "store": _host_of(u), "label": _host_of(u)})
            for u in urls]


def find_products(products: dict, part: str) -> list[dict]:
    part = (part or "").lower().strip()
    if not part:
        return []
    out = []
    for p in products.get("products", []):
        hay = f"{p['id']} {p['name']}".lower() + \
              "".join(o["url"].lower() for o in p["offers"])
        if part in hay:
            out.append(p)
    return out


def add_offer(product: dict, offer: dict) -> bool:
    """Attach an offer; False if that URL is already tracked on this product."""
    if any(o["url"] == offer["url"] for o in product["offers"]):
        return False
    product["offers"].append(_normalize_offer(offer))
    return True


def offer_entry(product: dict, offer: dict) -> dict:
    """Entry-shaped dict for per-offer alert formatting (alerts.py, telegram_io.py)."""
    return {"id": f"{product['id']}-{slugify(offer['store'])}",
            "name": product["name"], "store_label": offer.get("label") or offer["store"],
            "url": offer["url"]}


def best_result(results: Sequence[tuple[dict, parse.Extraction | None]]) \
        -> tuple[dict | None, parse.Extraction | None]:
    """Cheapest successful offer this run → (offer, extraction); (None, None) if all failed."""
    best_offer: dict | None = None
    best: parse.Extraction | None = None
    for offer, ex in results:
        if ex is None or ex.price is None:
            continue
        if best is None or best.price is None or ex.price < best.price:
            best_offer, best = offer, ex
    return best_offer, best


def best_state(product: dict, prices: dict) -> tuple[dict | None, dict | None]:
    """Offer with the lowest known price + its price state (for /prices, digest, charts)."""
    best_o: dict | None = None
    best_st: dict | None = None
    for o in product.get("offers") or []:
        st = prices.get(o["url"]) or {}
        p = st.get("price")
        if p is not None and (best_st is None or best_st.get("price") is None
                              or p < best_st.get("price")):
            best_o, best_st = o, st
    return best_o, best_st
