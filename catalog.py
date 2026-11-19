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
# kurpirkt is excluded: unreachable via ScraperAPI (verified 2026-11-19), so its
# offers would fail every CI run. Attach it manually only for local/home runs.
DISCOVERY_STORES = ("salidzini.lv", "ceno.lv")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _normalize_offer(offer: dict) -> dict:
    url = (offer.get("url") or "").strip()
    host = offer.get("store") or _host_of(url)
    return {"url": url, "store": host,
            "label": offer.get("label") or host}


def _normalize_tags(tags) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        tag = str(t).strip().lstrip("#").lower()
        if tag and tag not in out:
            out.append(tag)
    return out


def _normalize_product(prod: dict) -> dict:
    offers = [_normalize_offer(o) for o in prod.get("offers") or []]
    # de-duplicate by URL, keep first
    seen: set[str] = set()
    uniq = []
    for o in offers:
        if o["url"] and o["url"] not in seen:
            seen.add(o["url"])
            uniq.append(o)
    image = prod.get("image")
    return {
        "id": prod.get("id") or slugify(prod.get("name") or "item"),
        "name": (prod.get("name") or "").strip(),
        "threshold_pct": prod.get("threshold_pct"),
        "added": prod.get("added") or _now_iso(),
        "added_by": prod.get("added_by"),  # chat id of who /add-ed it (quota, removal rights)
        "atl": prod.get("atl"),          # {"price","date","url","store"} | None
        "last_min": prod.get("last_min"),  # {"price","url","store"} | None
        "tags": _normalize_tags(prod.get("tags")),  # phase 2 (Q24)
        "image": image if isinstance(image, str) and image.startswith(("http://", "https://"))
        else None,  # phase 2 (Q28): product photo for inline thumbs / the app
        "offers": uniq,
    }


def migrate_products(raw: dict | None) -> dict:
    """Accept v1 (flat url-entries) or v2; always return the v2 shape."""
    raw = raw or {}
    if raw.get("version") == CATALOG_VERSION:
        prods = [p for p in (_normalize_product(p) for p in raw.get("products") or [])
                 if p["offers"]]  # zero-offer products are merge artifacts — drop them
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
        f"https://ceno.lv/search?q={q}",
    ]
    return [_normalize_offer({"url": u, "store": _host_of(u), "label": _host_of(u)})
            for u in urls]


def find_products(products: dict, part: str) -> list[dict]:
    part = (part or "").lower().strip().lstrip("#")
    if not part:
        return []
    out = []
    for p in products.get("products", []):
        hay = f"{p['id']} {p['name']} #{'#'.join(p.get('tags') or [])}".lower() + \
              "".join(o["url"].lower() for o in p["offers"])
        if part in hay:
            out.append(p)
    return out


def find_tagged(products: dict, tag: str) -> list[dict]:
    """Exact-match tag filter for /list #audio — empty tag → everything."""
    tag = (tag or "").lower().strip().lstrip("#")
    if not tag:
        return list(products.get("products") or [])
    return [p for p in products.get("products") or [] if tag in (p.get("tags") or [])]


def tag_index(products: dict) -> dict[str, int]:
    """All tags → product counts (for /tags and the app's filter chips)."""
    idx: dict[str, int] = {}
    for p in products.get("products") or []:
        for t in p.get("tags") or []:
            idx[t] = idx.get(t, 0) + 1
    return dict(sorted(idx.items()))


def set_tag(product: dict, tag: str) -> bool:
    tag = (tag or "").strip().lstrip("#").lower()
    if not tag:
        return False
    tags = product.setdefault("tags", [])
    if tag in tags:
        return False
    tags.append(tag)
    return True


def unset_tag(product: dict, tag: str) -> bool:
    tag = (tag or "").strip().lstrip("#").lower()
    tags = product.get("tags") or []
    if tag not in tags:
        return False
    tags.remove(tag)
    return True


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


def product_stats(product: dict, prices: dict, now: datetime | None = None) -> dict:
    """Derived per-product stats for /stats, /best and the web snapshot.

    - best: current cheapest offer (price/store/url)
    - atl: stored all-time low (set by alerts.atl_update)
    - min7 / min30: cheapest single observation in the window (across all offers)
    - avg30: mean of all observations in the last 30 days (runs-weighted)
    """
    best_offer, best_st = best_state(product, prices)
    ts_now = (now or datetime.now(UTC)).timestamp()
    pts: list[tuple[float, float]] = []
    for o in product.get("offers") or []:
        for h in (prices.get(o["url"]) or {}).get("history") or []:
            if h.get("p") is None:
                continue
            try:
                ts = datetime.fromisoformat(h["t"]).timestamp()
            except ValueError:
                continue
            pts.append((ts, h["p"]))
    pts.sort()

    def window(op, seconds: int) -> float | None:
        vals = [p for t, p in pts if t >= ts_now - seconds]
        return op(vals) if vals else None

    def mean(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 2)

    store = (best_offer or {}).get("label") or (best_offer or {}).get("store")
    stats = {
        "best": {"price": best_st.get("price") if best_st else None,
                 "store": store, "url": (best_offer or {}).get("url")},
        "atl": product.get("atl"),
        "min7": window(min, 7 * 86400),
        "min30": window(min, 30 * 86400),
        "avg30": window(mean, 30 * 86400),
        "points": len(pts),
        "stores": len(product.get("offers") or []),
        # phase 2 (Q23) buy signals:
        "vol": volatility(pts, ts_now),      # ±% around the 30-day mean — is waiting worth it?
        "dow": best_weekday(pts, ts_now),    # cheapest weekday once ≥4 weeks of history exist
        "drops30": drop_count(pts, ts_now),  # how often patience paid in the last 30 days
    }
    return stats


def volatility(pts: list[tuple[float, float]], ts_now: float) -> float | None:
    """Relative volatility: stdev of 30-day observations as % of their mean.

    High (≈ >8%) = this product dips regularly, waiting often pays;
    low = buy whenever a decent drop appears.
    """
    vals = [p for t, p in pts if t >= ts_now - 30 * 86400]
    if len(vals) < 6:
        return None
    m = sum(vals) / len(vals)
    if m <= 0:
        return None
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return round(var ** 0.5 / m * 100, 1)


def best_weekday(pts: list[tuple[float, float]], ts_now: float) -> dict | None:
    """Cheapest day-of-week from ~4+ weeks of daily lows; None until honest data exists.

    Weekly lows (the cheapest observation per ISO week) are grouped by weekday so
    single cheap days can't dominate.
    """
    recent = [(t, p) for t, p in pts if t >= ts_now - 42 * 86400]
    if not recent:
        return None
    weeks: dict[str, float] = {}
    for t, p in recent:
        key = datetime.fromtimestamp(t, UTC).strftime("%G-W%V")
        weeks[key] = min(p, weeks.get(key, p))
    if len(weeks) < 4:
        return None
    per_dow: dict[int, list[float]] = {}
    for key, p in weeks.items():
        # Monday=0 … Sunday=6 from the week's Monday
        mon = datetime.strptime(key + "-1", "%G-W%V-%u").replace(tzinfo=UTC)
        per_dow.setdefault(mon.weekday(), []).append(p)
    if not per_dow:
        return None
    day, prices = min(per_dow.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
    return {"day": day, "avg": round(sum(prices) / len(prices), 2)}


def drop_count(pts: list[tuple[float, float]], ts_now: float,
               min_pct: float = 5.0) -> int | None:
    """Number of ≥min_pct price drops in the last 30 days ('did patience pay?')."""
    recent = sorted((t, p) for t, p in pts if t >= ts_now - 30 * 86400)
    if len(recent) < 2:
        return None
    drops = 0
    prev = recent[0][1]
    for _, p in recent[1:]:
        if prev and p < prev and (prev - p) / prev * 100 >= min_pct:
            drops += 1
            prev = p  # count successive drops from the new floor
        else:
            prev = min(prev, p) if prev else p
    return drops
