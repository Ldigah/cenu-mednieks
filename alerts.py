"""Diffing old vs new extractions → alert events; HTML message formatting.

Product-level policy (2026-11-19 rework): the product's best (min) offer drives
drop alerts (per-product threshold, default 5%) and all-time-low alerts.
Offer-level events (new/deeper discounts, restocks) are kept per store.
Price increases are recorded in state but never alerted.
"""
from __future__ import annotations

import html
from datetime import UTC, datetime

from config import DEFAULT_DROP_PCT, DROP_ALERT_MIN_PCT
from parse import Extraction


def esc(s) -> str:
    return html.escape(str(s))


def fmt_eur(v) -> str:
    return f"{v:.2f} €" if v is not None else "n/a"


def product_threshold(product: dict) -> float:
    t = product.get("threshold_pct")
    return float(t) if t is not None else DEFAULT_DROP_PCT


def entry_label(e: dict) -> str:
    return f"{e['name']} · {e.get('store_label', '')}"


def _drop_events(entry: dict, new: Extraction, old: dict | None) -> list[str]:
    events: list[str] = []
    if old is None:
        return events
    label = esc(entry_label(entry))
    link = entry["url"]
    prev = old.get("price")
    if new.price is not None and prev is not None and new.price < prev:
        pct = (prev - new.price) / prev * 100 if prev else 0.0
        if pct >= DROP_ALERT_MIN_PCT and (prev - new.price) >= 0.01:
            events.append(
                f"📉 <b>{label}</b>\n{fmt_eur(prev)} → <b>{fmt_eur(new.price)}</b> (−{pct:.1f}%)\n{link}")
    return events


def _discount_events(entry: dict, new: Extraction, old: dict | None) -> list[str]:
    events: list[str] = []
    if old is None:
        return events
    label = esc(entry_label(entry))
    link = entry["url"]
    if new.old_price is not None and new.price is not None and new.old_price > new.price:
        now_disc = new.old_price - new.price
        old_disc = None
        if old.get("old_price") is not None and old.get("price") is not None:
            old_disc = old["old_price"] - old["price"]
        if old_disc is None or now_disc > old_disc + 0.005:  # new or deeper discount
            pct = now_disc / new.old_price * 100 if new.old_price else 0.0
            events.append(
                f"🏷 <b>{label}</b> discount: <b>{fmt_eur(new.price)}</b> "
                f"(reg. {fmt_eur(new.old_price)}, −{pct:.0f}%)\n{link}")
    return events


def _restock_events(entry: dict, new: Extraction, old: dict | None) -> list[str]:
    if old is None:
        return []
    if new.in_stock is True and old.get("in_stock") is False and new.price is not None:
        return [f"♻️ <b>{esc(entry_label(entry))}</b> back in stock at "
                f"<b>{fmt_eur(new.price)}</b>\n{entry['url']}"]
    return []


def diff_events(entry: dict, new: Extraction, old: dict | None) -> list[str]:
    """All alert-worthy changes for one offer (legacy v1 behavior — tests rely on it)."""
    return _drop_events(entry, new, old) + _discount_events(entry, new, old) \
        + _restock_events(entry, new, old)


def offer_events(entry: dict, new: Extraction, old: dict | None) -> list[str]:
    """Per-offer events used in the product model — drops are handled at product level
    (the min offer), so only discounts and restocks fire per store."""
    return _discount_events(entry, new, old) + _restock_events(entry, new, old)


def product_min_event(product: dict, offer: dict, new: Extraction,
                      prev_min: dict | None) -> str | None:
    """Drop alert on the product's best price across stores (per-product threshold)."""
    if prev_min is None or prev_min.get("price") is None:
        return None
    if new.price is None or offer is None:
        return None
    prev_p = prev_min["price"]
    if new.price >= prev_p:
        return None
    pct = (prev_p - new.price) / prev_p * 100 if prev_p else 0.0
    if pct < product_threshold(product) or (prev_p - new.price) < 0.01:
        return None
    store = esc(offer.get("label") or offer.get("store") or "")
    prev_store = esc(prev_min.get("store") or "")
    prev_at = f" at {prev_store}" if prev_store else ""
    return (f"📉 <b>{esc(product['name'])}</b> — new best <b>{fmt_eur(new.price)}</b> "
            f"at {store}\nwas {fmt_eur(prev_p)}{prev_at} (−{pct:.1f}%)\n{offer['url']}")


def atl_update(product: dict, offer: dict, new: Extraction) -> str | None:
    """Record/refresh the product's all-time low. Returns an alert when a NEW low was
    set (the first sighting only establishes the baseline, silently)."""
    if new.price is None or offer is None:
        return None
    atl = product.get("atl")
    store = offer.get("label") or offer.get("store") or ""
    if not atl or atl.get("price") is None:
        product["atl"] = {"price": new.price,
                          "date": datetime.now(UTC).isoformat(timespec="seconds"),
                          "url": offer["url"], "store": store}
        return None
    if new.price < atl["price"]:
        prev_p = atl["price"]
        prev_date = str(atl.get("date", ""))[:10]
        product["atl"] = {"price": new.price,
                          "date": datetime.now(UTC).isoformat(timespec="seconds"),
                          "url": offer["url"], "store": store}
        return (f"🏆 <b>{esc(product['name'])}</b> all-time low: <b>{fmt_eur(new.price)}</b> "
                f"at {esc(store)}\nprevious low {fmt_eur(prev_p)} from {prev_date}\n{offer['url']}")
    return None


def failure_alert(entry: dict, fails: int, reason: str) -> str:
    label = esc(entry_label(entry))
    return (f"⚠️ <b>{label}</b> failed {fails}× in a row — {esc(reason)}.\n"
            f"Selectors may need fixing (scraper-doctor). {entry['url']}")


def _delta_7d(st: dict, now: datetime) -> str | None:
    """Δ vs the price ~7 days ago (closest history point), for the digest."""
    hist = [h for h in st.get("history") or [] if h.get("p") is not None]
    if len(hist) < 2:
        return None
    target = now.timestamp() - 7 * 86400
    try:
        pts = [(datetime.fromisoformat(h["t"]).timestamp(), h["p"]) for h in hist]
    except ValueError:
        return None
    old_pts = [p for p in pts if p[0] <= target]
    ref = min(old_pts, key=lambda p: abs(p[0] - target)) if old_pts else pts[0]
    cur = st.get("price")
    if cur is None or ref[1] is None or cur == ref[1]:
        return None
    d = cur - ref[1]
    arrow = "▲" if d > 0 else "▼"
    return f" {arrow}{abs(d):.2f} €/7d"


def digest_lines(products: dict, prices: dict) -> list[str]:
    """Enriched daily digest: best store per product, ATL, 7-day delta, fail counts."""
    now = datetime.now(UTC)
    lines = [f"🛒 <b>Daily price summary</b> — {now:%Y-%m-%d} (all times UTC in history)"]
    for prod in products.get("products", []):
        offers = prod.get("offers") or []
        best_offer: dict | None = None
        best_st: dict = {}
        best: float | None = None
        for o in offers:
            st = prices.get(o["url"], {}) or {}
            p = st.get("price")
            if p is not None and (best is None or p < best):
                best_offer, best_st, best = o, st, p
        fails = sum(int((prices.get(o["url"], {}) or {}).get("fails") or 0) for o in offers)
        if best_offer is None or best is None:
            tail = f" ⚠️ no data ({fails} fails)" if fails else " ⚠️ no data yet"
            lines.append(f"• <b>{esc(prod['name'] or prod['id'])}</b>{tail}")
            continue
        badge = "✅" if best_st.get("in_stock") else ("❌" if best_st.get("in_stock") is False else "•")
        store = best_offer.get("label") or best_offer.get("store") or ""
        old = best_st.get("old_price")
        extra = f" <i>(reg. {fmt_eur(old)})</i>" if old and best and old > best else ""
        conv = (f" <i>(≈ from {esc(best_st['display'])})</i>"
                if best_st.get("converted") and best_st.get("display") else "")
        delta = _delta_7d(best_st, now) or ""
        atl = prod.get("atl")
        atl_txt = (f" · 🏆 {fmt_eur(atl['price'])}" if atl and atl.get("price") is not None
                   and atl["price"] < best else "")
        fails_txt = f" · ⚠️×{fails}" if fails else ""
        n = len(offers)
        lines.append(
            f"{badge} <a href=\"{best_offer['url']}\">{esc(prod['name'] or prod['id'])}</a>: "
            f"<b>{fmt_eur(best)}</b> at {esc(store)}{extra}{conv}{delta}{atl_txt}{fails_txt} "
            f"({n} store{'s' if n != 1 else ''})")
    if len(lines) == 1:
        lines.append("No products tracked yet — send /add &lt;name&gt;.")
    return lines


def health_summary(products: dict, prices: dict) -> str:
    """Failures-only report across all offers of all products."""
    total = 0
    failing = []
    for prod in products.get("products", []):
        for o in prod.get("offers") or []:
            total += 1
            st = prices.get(o["url"], {})
            if st.get("fails"):
                failing.append(f"⚠️ {esc(prod['name'])} · {esc(o.get('label') or o['store'])} "
                               f"×{st['fails']}")
    if not failing:
        return f"🩺 <b>Weekly health check:</b> all {total} offers OK ✅"
    return (f"🩺 <b>Weekly health check:</b> {len(failing)}/{total} offers failing\n"
            + "\n".join(failing))
