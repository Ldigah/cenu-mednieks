"""Diffing old vs new extractions → alert events; HTML message formatting.

Product-level policy (2026-11-19 rework): the product's best (min) offer drives
drop alerts (per-product threshold, default 5%) and all-time-low alerts.
Offer-level events (new/deeper discounts, restocks) are kept per store.
Price increases are recorded in state but never alerted.

Multi-user additions (the 40 questions, 2026-11-19): per-user routing +
quiet-hour holds, personal digests, and ScraperAPI budget tracking (Q38).
"""
from __future__ import annotations

import html
from datetime import UTC, datetime

import i18n
import users as users_mod
from config import DEFAULT_DROP_PCT, DROP_ALERT_MIN_PCT, SCRAPERAPI_MONTHLY_CREDITS, USAGE_WARN_FRACTION
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
    out = []
    f = _discount_fields(entry, new, old)
    if f:
        out.append(f"🏷 <b>{f['label']}</b> discount: <b>{f['price']}</b> "
                   f"(reg. {f['old']}, −{f['pct']}%)\n{f['url']}")
    f = _restock_fields(entry, new, old)
    if f:
        out.append(f"♻️ <b>{f['label']}</b> back in stock at <b>{f['price']}</b>\n{f['url']}")
    return out


def offer_event_specs(entry: dict, new: Extraction, old: dict | None) -> list[tuple[str, dict]]:
    """i18n specs for the per-offer events (same triggers as offer_events)."""
    specs = []
    f = _discount_fields(entry, new, old)
    if f:
        specs.append(("discount_alert", f))
    f = _restock_fields(entry, new, old)
    if f:
        specs.append(("restock_alert", f))
    return specs


def _discount_fields(entry: dict, new: Extraction, old: dict | None) -> dict | None:
    if old is None:
        return None
    if new.old_price is not None and new.price is not None and new.old_price > new.price:
        now_disc = new.old_price - new.price
        old_disc = None
        if old.get("old_price") is not None and old.get("price") is not None:
            old_disc = old["old_price"] - old["price"]
        if old_disc is None or now_disc > old_disc + 0.005:  # new or deeper discount
            pct = now_disc / new.old_price * 100 if new.old_price else 0.0
            return {"label": esc(entry_label(entry)),
                    "price": fmt_eur(new.price), "old": fmt_eur(new.old_price),
                    "pct": f"{pct:.0f}", "url": entry["url"]}
    return None


def _restock_fields(entry: dict, new: Extraction, old: dict | None) -> dict | None:
    if old is None:
        return None
    if new.in_stock is True and old.get("in_stock") is False and new.price is not None:
        return {"label": esc(entry_label(entry)), "price": fmt_eur(new.price),
                "url": entry["url"]}
    return None


# --- target prices (Q21) -----------------------------------------------------

def target_event(product: dict, price: float, target: float, store: str) -> str:
    """English target-hit alert (legacy builder)."""
    return (f"🎯 <b>{esc(product['name'])}</b> hit your target: <b>{fmt_eur(price)}</b> "
            f"(target {fmt_eur(target)}) at {esc(store)}\n"
            f"{(product.get('last_min') or {}).get('url') or ''}")


def target_spec(product: dict, price: float, target: float, store: str) -> tuple[str, dict]:
    return ("target_alert", {"name": esc(product["name"]), "price": fmt_eur(price),
                             "target": fmt_eur(target), "store": esc(store),
                             "url": (product.get("last_min") or {}).get("url") or ""})


def product_min_event(product: dict, offer: dict, new: Extraction,
                      prev_min: dict | None, own_prev: float | None = None) -> str | None:
    """Drop alert on the product's best price across stores (per-product threshold).

    `own_prev` is the winning offer's own last seen price. `prev_min` is only the
    previous run's best, so when the cheapest source failed that run and now returns
    unchanged, the gap to the dearer store that stood in reads as a drop (JBL: 73 €
    after a run recorded at 77 € is −5.2%, over the default threshold).
    """
    f = _product_min_fields(product, offer, new, prev_min, own_prev)
    if f is None:
        return None
    prev_at = f" at {f['prev_store']}" if f["prev_store"] else ""
    return (f"📉 <b>{f['name']}</b> — new best <b>{f['price']}</b> "
            f"at {f['store']}\nwas {f['prev']}{prev_at} (−{f['pct']}%)\n{f['url']}")


def _product_min_fields(product: dict, offer: dict, new: Extraction,
                        prev_min: dict | None, own_prev: float | None = None) -> dict | None:
    """Shared trigger + fields for the product drop alert (None = no alert)."""
    if prev_min is None or prev_min.get("price") is None:
        return None
    if new.price is None or offer is None:
        return None
    if own_prev is not None and new.price >= own_prev:
        return None              # merely back at its own last price — nothing dropped
    prev_p = prev_min["price"]
    if new.price >= prev_p:
        return None
    pct = (prev_p - new.price) / prev_p * 100 if prev_p else 0.0
    if pct < product_threshold(product) or (prev_p - new.price) < 0.01:
        return None
    return {"name": esc(product["name"]),
            "price": fmt_eur(new.price),
            "store": esc(offer.get("label") or offer.get("store") or ""),
            "prev": fmt_eur(prev_p),
            "prev_store": esc(prev_min.get("store") or ""),
            "pct": f"{pct:.1f}",
            "url": offer["url"]}


def product_min_spec(product: dict, offer: dict, new: Extraction,
                     prev_min: dict | None, own_prev: float | None = None) -> tuple[str, dict] | None:
    """i18n spec for the drop alert — same trigger, T()-rendered per user language."""
    f = _product_min_fields(product, offer, new, prev_min, own_prev)
    return ("drop_alert", f) if f else None


def atl_update(product: dict, offer: dict, new: Extraction) -> str | None:
    """Record/refresh the product's all-time low. Returns an alert when a NEW low was
    set (the first sighting only establishes the baseline, silently)."""
    f = _atl_fields(product, offer, new)
    if f is None:
        return None
    if f["kind"] == "baseline":
        return None
    return (f"🏆 <b>{f['name']}</b> all-time low: <b>{f['price']}</b> "
            f"at {f['store']}\nprevious low {f['prev']} from {f['prev_date']}\n{f['url']}")


def _atl_fields(product: dict, offer: dict, new: Extraction) -> dict | None:
    """Shared trigger + fields for the ATL event; also WRITES the new product.atl."""
    if new.price is None or offer is None:
        return None
    atl = product.get("atl")
    store = offer.get("label") or offer.get("store") or ""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if not atl or atl.get("price") is None:
        product["atl"] = {"price": new.price, "date": now,
                          "url": offer["url"], "store": store}
        return {"kind": "baseline"}
    if new.price < atl["price"]:
        prev_p = atl["price"]
        prev_date = str(atl.get("date", ""))[:10]
        product["atl"] = {"price": new.price, "date": now,
                          "url": offer["url"], "store": store}
        return {"kind": "alert",
                "name": esc(product["name"]), "price": fmt_eur(new.price),
                "store": esc(store), "prev": fmt_eur(prev_p),
                "prev_date": prev_date, "url": offer["url"]}
    return None


def atl_spec(product: dict, offer: dict, new: Extraction) -> tuple[str, dict] | None:
    """i18n spec for the all-time-low alert (baseline sightings alert nothing)."""
    f = _atl_fields(product, offer, new)
    if f is None or f["kind"] == "baseline":
        return None
    return ("atl_alert", f)


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


def digest_lines(products: dict, prices: dict, ids: list[str] | None = None,
                 lang: str | None = None) -> list[str]:
    """Enriched daily digest: best store per product, ATL, 7-day delta, fail counts.

    ids: optional watchlist filter — personal digests pass the user's product ids (Q20).
    lang: user preference — header/empty note localized (rows stay data-English).
    """
    now = datetime.now(UTC)
    lines = [i18n.T(lang, "digest_header", date=f"{now:%Y-%m-%d}")
             + " (all times UTC in history)"]
    for prod in products.get("products", []):
        if ids is not None and prod.get("id") not in ids:
            continue
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
        lines.append(i18n.T(lang, "digest_empty"))
    return lines


# ---------------------------------------------------------------------------
# Multi-user routing + quiet hours (Q3/Q19) and ScraperAPI budget (Q38)
# ---------------------------------------------------------------------------

def recipients(ud: dict, product_id: str) -> list[dict]:
    """Active users who should hear about this product right now (watch-aware)."""

    return [u for u in users_mod.active(ud) if users_mod.wants_alert(u, product_id)]


def hold_alert(ud: dict, user: dict, text: str, keyboard, bot_state: dict,
               now: datetime | None = None) -> bool:
    """Quiet hours (Q19): park the alert in bot_state.pending_alerts for a morning flush.

    Returns True when the alert was held (caller should not send it now).
    """

    if not users_mod.is_quiet_now(user, now or datetime.now(UTC)):
        return False
    pend = bot_state.setdefault("pending_alerts", {})
    pend.setdefault(str(user["chat_id"]), []).append({"text": text, "kb": keyboard})
    return True


def flush_held(ud: dict, bot_state: dict, now: datetime | None = None) -> dict:
    """Pending alerts grouped per chat whose quiet window has ended (Q19).

    Held entries are popped from bot_state.pending_alerts and returned as
    {chat_id: [{"text", "kb"}]} for the caller to deliver with an
    "🌙 overnight" prefix. Chats still inside their quiet window stay parked.
    """

    pend = bot_state.get("pending_alerts") or {}
    now = now or datetime.now(UTC)
    out: dict = {}
    for chat_id_s, items in list(pend.items()):
        u = users_mod.get(ud, int(chat_id_s))
        if u is None or not users_mod.is_quiet_now(u, now):
            if items:
                out[int(chat_id_s)] = items
            pend.pop(chat_id_s, None)
    return out


def bump_usage(usage: dict, credits: int = 1, now: datetime | None = None) -> dict:
    """Count ScraperAPI credits into bot_state.usage, rolling over per month (Q38)."""
    month = (now or datetime.now(UTC)).strftime("%Y-%m")
    if usage.get("month") != month:
        usage.clear()
        usage["month"] = month
        usage["credits"] = 0
        usage["warned"] = False
    usage["credits"] = int(usage.get("credits") or 0) + credits
    return usage


def budget_line(usage: dict, lang: str | None = None) -> str | None:
    """Digest footer: credit usage this month; None when nothing was spent."""
    if usage.get("month") != datetime.now(UTC).strftime("%Y-%m"):
        return None
    credits = int(usage.get("credits") or 0)
    if credits <= 0:
        return None
    return i18n.T(lang, "budget_line", credits=credits, total=SCRAPERAPI_MONTHLY_CREDITS)


def budget_warning(usage: dict) -> str | None:
    """One-time owner ping at 80% of the free tier (Q38)."""
    if usage.get("month") != datetime.now(UTC).strftime("%Y-%m") or usage.get("warned"):
        return None
    credits = int(usage.get("credits") or 0)
    if credits < SCRAPERAPI_MONTHLY_CREDITS * USAGE_WARN_FRACTION:
        return None
    usage["warned"] = True
    left = SCRAPERAPI_MONTHLY_CREDITS - credits
    return (f"💳 <b>ScraperAPI budget warning</b>: {credits}/"
            f"{SCRAPERAPI_MONTHLY_CREDITS} credits used this month "
            f"({left} left). New products and /search cost extra — "
            "kurpirkt stays local-only until the reset.")


def health_summary(products: dict, prices: dict) -> str:
    """Failures-only report across all offers of all products, each with its last error."""
    total = 0
    failing = []
    for prod in products.get("products", []):
        for o in prod.get("offers") or []:
            total += 1
            st = prices.get(o["url"], {})
            if st.get("fails"):
                reason = f" — {esc(str(st['last_error'])[:90])}" if st.get("last_error") else ""
                failing.append(f"⚠️ {esc(prod['name'])} · {esc(o.get('label') or o['store'])} "
                               f"×{st['fails']}{reason}")
    if not failing:
        return f"🩺 <b>Weekly health check:</b> all {total} offers OK ✅"
    return (f"🩺 <b>Weekly health check:</b> {len(failing)}/{total} offers failing\n"
            + "\n".join(failing))
