"""Diffing old vs new extractions → alert events; HTML message formatting."""
from __future__ import annotations

import html
from datetime import UTC, datetime

from config import DROP_ALERT_MIN_PCT
from parse import Extraction


def esc(s) -> str:
    return html.escape(str(s))


def fmt_eur(v) -> str:
    return f"{v:.2f} €" if v is not None else "n/a"


def diff_events(entry: dict, new: Extraction, old: dict | None) -> list[str]:
    """Alert-worthy changes for one entry.

    Policy (QUESTIONS.md answers): drops ≥1%, new/deeper discounts, restocks.
    Price increases are recorded in state but never alerted.
    """
    events: list[str] = []
    if old is None:
        return events  # first sighting: no alert
    label = esc(f"{entry['name']} · {entry.get('store_label', '')}")
    link = entry["url"]

    prev = old.get("price")
    if new.price is not None and prev is not None and new.price < prev:
        pct = (prev - new.price) / prev * 100 if prev else 0.0
        if pct >= DROP_ALERT_MIN_PCT and (prev - new.price) >= 0.01:
            events.append(
                f"📉 <b>{label}</b>\n{fmt_eur(prev)} → <b>{fmt_eur(new.price)}</b> (−{pct:.1f}%)\n{link}")

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

    if new.in_stock is True and old.get("in_stock") is False and new.price is not None:
        events.append(f"♻️ <b>{label}</b> back in stock at <b>{fmt_eur(new.price)}</b>\n{link}")

    return events


def failure_alert(entry: dict, fails: int, reason: str) -> str:
    label = esc(f"{entry['name']} · {entry.get('store_label', '')}")
    return (f"⚠️ <b>{label}</b> failed {fails}× in a row — {esc(reason)}.\n"
            f"Selectors may need fixing (scraper-doctor). {entry['url']}")


def digest_lines(products: list[dict], prices: dict) -> list[str]:
    now = datetime.now(UTC)
    lines = [f"🛒 <b>Daily price summary</b> — {now:%Y-%m-%d} (all times UTC in history)"]
    for e in products:
        st = prices.get(e["url"], {})
        price, old, stock = st.get("price"), st.get("old_price"), st.get("in_stock")
        badge = "✅" if stock else ("❌" if stock is False else "•")
        extra = f" <i>(reg. {fmt_eur(old)})</i>" if old and price and old > price else ""
        conv = (f" <i>(≈ from {esc(st['display'])})</i>"
                if st.get("converted") and st.get("display") else "")
        warn = (" ⚠️ non-EUR" if st.get("currency") not in (None, "EUR")
                and not st.get("converted") else "")
        fails = f" ⚠️×{st['fails']}" if st.get("fails") else ""
        shop = f" · {esc(st['shop'])}" if st.get("shop") else ""
        lines.append(
            f"{badge} <a href=\"{e['url']}\">{esc(e['name'] or e['id'])}</a>"
            f" · {esc(e.get('store_label', ''))}{shop}: <b>{fmt_eur(price)}</b>{extra}{conv}{warn}{fails}")
    if len(lines) == 1:
        lines.append("No products tracked yet — send /add &lt;url&gt; [name].")
    return lines


def health_summary(products: list[dict], prices: dict) -> str:
    total = len(products)
    failing = []
    for e in products:
        st = prices.get(e["url"], {})
        if st.get("fails"):
            failing.append(f"⚠️ {esc(commands_label(e))} ×{st['fails']}")
    if not failing:
        return f"🩺 <b>Weekly health check:</b> all {total} entries OK ✅"
    return (f"🩺 <b>Weekly health check:</b> {len(failing)}/{total} entries failing\n"
            + "\n".join(failing))


def commands_label(e: dict) -> str:
    return f"{e['name']} · {e.get('store_label', '')}"
