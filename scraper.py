"""Cenu Mednieks — entry point.

Usage:
  python scraper.py                # full run (CI): commands → scrape → alerts → digest → save
  python scraper.py --local        # local dry run: state-local/, no Telegram
  python scraper.py --only PART    # only entries whose name/id contains PART
  python scraper.py --health       # weekly health check: report failures only
  python scraper.py --digest       # force the daily digest
  python scraper.py --no-digest    # suppress the digest
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

import alerts
import fetch
import state as state_mod
import stores
import telegram_io
from config import (DIGEST_RUN_HOUR_UTC, FAIL_ALERT_AFTER, F_BOT, F_PRICES, F_PRODUCTS,
                    GIST_ID, GIST_TOKEN, HISTORY_MAX_POINTS,
                    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

log = logging.getLogger("tracker")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _page_title(html_text: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, "html.parser")
    t = soup.find("title")
    return (t.get_text(strip=True) if t else "")[:120]


def _extract_or_diag(entry: dict, html_text: str, adapter):
    """Extract, and log diagnostics when no price is found (helps tell block vs drift)."""
    ex = adapter.extract(html_text)
    if ex.price is None:
        log.warning("diag %s: len=%d title=%r blocked=%r",
                    entry["id"], len(html_text), _page_title(html_text),
                    fetch._looks_blocked(html_text))
    return ("price not found on page" if ex.price is None else None), ex


def scrape_entry(entry: dict):
    """→ (error: str | None, extraction: parse.Extraction | None)."""
    adapter = stores.adapter_for(entry["url"])
    try:
        if adapter.uses_playwright():
            return _extract_or_diag(
                entry, fetch.fetch_playwright(entry["url"], adapter.wait_selector), adapter)
        try:
            html_text = fetch.fetch_requests(entry["url"])
        except fetch.FetchError:
            if adapter.strategy == "requests+playwright":
                log.info("%s: requests failed, trying playwright", entry["id"])
                html_text = fetch.fetch_playwright(entry["url"], adapter.wait_selector)
            else:
                raise
        err, ex = _extract_or_diag(entry, html_text, adapter)
        if ex.price is None and adapter.strategy == "requests+playwright":
            log.info("%s: no price via requests, trying playwright", entry["id"])
            html2 = fetch.fetch_playwright(entry["url"], adapter.wait_selector)
            return _extract_or_diag(entry, html2, adapter)
        return err, ex
    except fetch.FetchError as e:
        return str(e), None


def bootstrap_gist() -> int:
    """First run without GIST_ID: create the state gist and explain what to do next."""
    files = {
        F_PRODUCTS: state_mod.seed_products(),
        F_PRICES: {},
        F_BOT: {"update_offset": 0},
    }
    gist_id = state_mod.create_gist(GIST_TOKEN, files)
    msg = (f"🆕 State gist created: GIST_ID={gist_id}\n"
           "Add it as a GitHub Actions secret named GIST_ID, then re-run the workflow "
           "(or add it to your local .env).")
    print("=" * 60)
    print(f"GIST_ID={gist_id}")
    print("=" * 60)
    log.info("%s", msg)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            telegram_io.send_message(TELEGRAM_CHAT_ID, msg)
        except Exception as e:  # noqa: BLE001 — best effort only
            log.warning("could not notify telegram: %s", e)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Cenu Mednieks price tracker")
    ap.add_argument("--local", action="store_true", help="local dry run (no gist, no Telegram)")
    ap.add_argument("--only", metavar="PART", help="scrape only matching entries")
    ap.add_argument("--health", action="store_true", help="health check: report failures only")
    ap.add_argument("--digest", action="store_true", help="force daily digest")
    ap.add_argument("--no-digest", action="store_true", help="suppress daily digest")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    if not args.local and not GIST_TOKEN:
        print("FATAL: GIST_TOKEN is not set — see SETUP.md", file=sys.stderr)
        return 1
    if not args.local and not GIST_ID:
        return bootstrap_gist()

    store = state_mod.default_store(args.local)
    products = store.read(F_PRODUCTS) or state_mod.seed_products()
    products.setdefault("products", [])
    prices = store.read(F_PRICES) or {}
    bot_state = store.read(F_BOT) or {"update_offset": 0}
    first_run = not prices

    # --- 1. Telegram commands (added products get scraped on this very run) ---
    chat_id = None
    if not args.local and TELEGRAM_BOT_TOKEN:
        chat_id = TELEGRAM_CHAT_ID or bot_state.get("chat_id")
        try:
            updates = telegram_io.get_updates(int(bot_state.get("update_offset") or 0))
            for u in updates:
                bot_state["update_offset"] = max(int(bot_state.get("update_offset") or 0),
                                                 u.get("update_id", 0))
                telegram_io.handle_update(u, products, prices, bot_state)
        except Exception as e:  # noqa: BLE001 — commands must not kill the run
            log.warning("command processing failed: %s", e)
        if not chat_id:
            chat_id = bot_state.get("chat_id") or TELEGRAM_CHAT_ID

    send = bool(chat_id) and bool(TELEGRAM_BOT_TOKEN) and not args.local

    # --- 2. Scrape ---
    ok = failed = 0
    for entry in products["products"]:
        if args.only and args.only.lower() not in f"{entry['id']} {entry['name']}".lower():
            continue
        err, ex = scrape_entry(entry)
        st = prices.setdefault(entry["url"], {})

        if err is not None or ex is None or ex.price is None:
            st["fails"] = int(st.get("fails") or 0) + 1
            failed += 1
            log.warning("FAIL %s (×%d): %s", entry["id"], st["fails"], err)
            if send and not args.health and st["fails"] == FAIL_ALERT_AFTER:
                telegram_io.send_message(chat_id, alerts.failure_alert(entry, st["fails"], err or "?"))
            continue

        st["fails"] = 0
        prev = {"price": st.get("price"), "old_price": st.get("old_price"),
                "in_stock": st.get("in_stock")} if st.get("price") is not None else None
        if not args.health and send:
            events = alerts.diff_events(entry, ex, prev)
            if events:
                telegram_io.send_message(chat_id, "\n\n".join(events))

        st.update(price=ex.price, old_price=ex.old_price, in_stock=ex.in_stock,
                  shop=ex.shop, offers=ex.offers, display=ex.display,
                  currency=ex.currency, converted=ex.converted, last_checked=now_iso())
        st.setdefault("first_seen", now_iso())
        hist = st.setdefault("history", [])
        hist.append({"t": now_iso(), "p": ex.price})
        if len(hist) > HISTORY_MAX_POINTS:
            del hist[: len(hist) - HISTORY_MAX_POINTS]
        ok += 1
        log.info("OK   %s: %s (reg. %s) stock=%s", entry["id"], ex.price, ex.old_price, ex.in_stock)
        time.sleep(5)  # politeness gap — pigu.lt throttles bursts; gentle on everyone

    # --- 3. Reports ---
    hour = datetime.now(timezone.utc).hour
    want_digest = (args.digest or (hour == DIGEST_RUN_HOUR_UTC)) and not args.no_digest
    if send and not args.health:
        if first_run:
            telegram_io.send_message(
                chat_id, f"✅ <b>Cenu Mednieks initialized</b> — tracking "
                         f"{len(products['products'])} entries ({ok} ok, {failed} failed).")
        if want_digest and products["products"]:
            telegram_io.send_message(chat_id, "\n".join(alerts.digest_lines(products["products"], prices)))
    if args.health:
        summary = alerts.health_summary(products["products"], prices)
        print(summary)
        if send and "⚠️" in summary:
            telegram_io.send_message(chat_id, summary)

    # --- 4. Persist ---
    store.write(F_PRODUCTS, products)
    store.write(F_PRICES, prices)
    store.write(F_BOT, bot_state)
    log.info("done: %d ok, %d failed", ok, failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
