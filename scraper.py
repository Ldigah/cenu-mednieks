"""Cenu Mednieks — entry point.

Usage:
  python scraper.py                # full run (CI): commands → scrape → alerts → digest → save
  python scraper.py --local        # local dry run: state-local/, no Telegram
  python scraper.py --only PART    # only products whose name/id contains PART
  python scraper.py --stores DOMS  # only offers on these store domains, comma-separated
  python scraper.py --skip-stores DOMS  # skip offers on these store domains
  python scraper.py --health       # weekly health check: report failures only
  python scraper.py --digest       # force the daily digest
  python scraper.py --no-digest    # suppress the digest
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import alerts
import catalog
import fetch
import parse
import state as state_mod
import stores
import telegram_io
from config import (
    DIGEST_RUN_HOUR_UTC,
    F_BOT,
    F_LOCK,
    F_PRICES,
    F_PRODUCTS,
    FAIL_ALERT_AFTER,
    GIST_ID,
    GIST_TOKEN,
    HISTORY_MAX_POINTS,
    SCRAPERAPI_KEY,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

log = logging.getLogger("tracker")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _page_title(html_text: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, "html.parser")
    t = soup.find("title")
    return (t.get_text(strip=True) if t else "")[:120]


def _extract_or_diag(ident: str, html_text: str, adapter):
    """Extract, and log diagnostics when no price is found (helps tell block vs drift)."""
    ex = adapter.extract(html_text)
    if ex is None or ex.price is None:
        log.warning("diag %s: len=%d title=%r blocked=%r snippet=%r",
                    ident, len(html_text), _page_title(html_text),
                    fetch._looks_blocked(html_text), html_text[:200])
    return ("price not found on page" if ex is None or ex.price is None else None), ex


def scrape_offer(url: str, adapter: stores.StoreAdapter):
    """Fetch + extract one offer → (error: str | None, extraction | None).

    Cloudflare-guarded aggregators go through ScraperAPI in CI (dc_blocked presets);
    everything else uses the requests → Playwright ladder.
    """
    ident = urlparse(url).hostname or url
    try:
        if SCRAPERAPI_KEY and adapter.dc_blocked:
            html_text = fetch.fetch_scraperapi(url, SCRAPERAPI_KEY,
                                               render=adapter.render_api,
                                               country=adapter.api_country)
            return _extract_or_diag(ident, html_text, adapter)
        if adapter.uses_playwright():
            return _extract_or_diag(
                ident, fetch.fetch_playwright(url, adapter.wait_selector), adapter)
        try:
            html_text = fetch.fetch_requests(url)
        except fetch.FetchError:
            if adapter.strategy == "requests+playwright":
                log.info("%s: requests failed, trying playwright", ident)
                html_text = fetch.fetch_playwright(url, adapter.wait_selector)
            else:
                raise
        err, ex = _extract_or_diag(ident, html_text, adapter)
        if (err is not None) and adapter.strategy == "requests+playwright":
            log.info("%s: no price via requests, trying playwright", ident)
            html2 = fetch.fetch_playwright(url, adapter.wait_selector)
            return _extract_or_diag(ident, html2, adapter)
        return err, ex
    except fetch.FetchError as e:
        return str(e), None


def _checked_today(st: dict) -> bool:
    last = str(st.get("last_checked") or "")[:10]
    return bool(last) and last == datetime.now(UTC).date().isoformat()


def bootstrap_gist() -> int:
    """First run without GIST_ID: create the state gist and explain what to do next."""
    files = {
        F_PRODUCTS: catalog.migrate_products(state_mod.seed_products()),
        F_PRICES: {},
        F_BOT: {"update_offset": 0},
        F_LOCK: {},
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
    ap.add_argument("--only", metavar="PART", help="scrape only products matching PART")
    ap.add_argument("--stores", metavar="DOMS",
                    help="only offers on these store domains, comma-separated (e.g. salidzini.lv,pigu.lt)")
    ap.add_argument("--skip-stores", metavar="DOMS",
                    help="skip offers on these store domains")
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
    products = catalog.migrate_products(store.read(F_PRODUCTS) or state_mod.seed_products())
    prices = store.read(F_PRICES) or {}
    bot_state = store.read(F_BOT) or {"update_offset": 0}
    first_run = not prices

    # --- 0. State lock (two runners may overlap: scheduled + manual dispatch) ---
    gist = isinstance(store, state_mod.GistStore)
    runner_id = os.environ.get("RUNNER_ID") or f"run-{os.getpid()}-{int(time.time())}"
    have_lock = False
    if gist:
        have_lock = store.acquire_lock(runner_id)
        if not have_lock:
            log.warning("state lock held by another run — waiting 20 s, then proceeding; "
                        "merge-writes keep this safe")
            time.sleep(20)
            have_lock = store.acquire_lock(runner_id)

    try:
        # --- 1. Telegram commands (added products get scraped on this very run) ---
        chat_id = None
        if not args.local and TELEGRAM_BOT_TOKEN:
            chat_id = TELEGRAM_CHAT_ID or bot_state.get("chat_id")
            try:
                me = telegram_io.get_me()
                if me:
                    log.info("telegram bot: @%s (id %s)", me.get("username"), me.get("id"))
                updates = telegram_io.get_updates(int(bot_state.get("update_offset") or 0))
                log.info("telegram: %d pending update(s)", len(updates))
                if not updates and not chat_id:
                    log.warning("no /start found — send /start to @%s in Telegram, then re-run",
                                (me or {}).get("username", "<unknown>"))
                for u in updates:
                    bot_state["update_offset"] = max(int(bot_state.get("update_offset") or 0),
                                                     u.get("update_id", 0))
                    telegram_io.handle_update(u, products, prices, bot_state)
            except Exception as e:  # noqa: BLE001 — commands must not kill the run
                log.warning("command processing failed: %s", e)
            if not chat_id:
                chat_id = bot_state.get("chat_id") or TELEGRAM_CHAT_ID

        send = bool(chat_id) and bool(TELEGRAM_BOT_TOKEN) and not args.local

        # --- 2. Scrape: product → offers; product-level events from the run's best ---
        ok = failed = 0
        only_stores = {s.strip().lower() for s in args.stores.split(",")} if args.stores else set()
        skip_stores = {s.strip().lower() for s in args.skip_stores.split(",")} if args.skip_stores else set()
        prev_host = None
        for product in products["products"]:
            if args.only and args.only.lower() not in f"{product['id']} {product['name']}".lower():
                continue
            results: list[tuple[dict, parse.Extraction | None]] = []
            for offer in product["offers"]:
                url = offer["url"]
                host = (urlparse(url).hostname or "").lower().removeprefix("www.")
                if only_stores and host not in only_stores:
                    continue
                if skip_stores and host in skip_stores:
                    continue
                adapter = stores.adapter_for(url)
                st = prices.setdefault(url, {})
                if adapter.daily_only and _checked_today(st):
                    log.info("SKIP %s · %s (daily cadence)", product["id"], offer["store"])
                    continue
                if host == prev_host:
                    time.sleep(15)  # same-store spacing — Cloudflare throttles bursts

                entry = catalog.offer_entry(product, offer)
                err, ex = scrape_offer(url, adapter)
                if err is not None or ex is None or ex.price is None:
                    st["fails"] = int(st.get("fails") or 0) + 1
                    failed += 1
                    log.warning("FAIL %s · %s (×%d): %s",
                                product["id"], offer["store"], st["fails"], err)
                    # alert at 3 consecutive fails, then re-nag every 5th so a dead
                    # offer never goes permanently quiet
                    nag = (st["fails"] == FAIL_ALERT_AFTER
                           or (st["fails"] > FAIL_ALERT_AFTER
                               and (st["fails"] - FAIL_ALERT_AFTER) % 5 == 0))
                    if send and not args.health and nag:
                        telegram_io.send_message(
                            chat_id, alerts.failure_alert(entry, st["fails"], err or "?"))
                    results.append((offer, None))
                else:
                    st["fails"] = 0
                    prev = {"price": st.get("price"), "old_price": st.get("old_price"),
                            "in_stock": st.get("in_stock")} if st.get("price") is not None else None
                    if not args.health and send:
                        events = alerts.offer_events(entry, ex, prev)
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
                    log.info("OK   %s · %s: %s (reg. %s) stock=%s [%s]",
                             product["id"], offer["store"], ex.price, ex.old_price,
                             ex.in_stock, ex.display)
                    results.append((offer, ex))
                prev_host = host
                time.sleep(5)  # politeness gap — gentle on everyone

            # product-level: best price this run drives drop + all-time-low alerts
            best_offer, best = catalog.best_result(results)
            if best is not None and best_offer is not None:
                events = []
                ev = alerts.product_min_event(product, best_offer, best, product.get("last_min"))
                if ev:
                    events.append(ev)
                ev = alerts.atl_update(product, best_offer, best)
                if ev:
                    events.append(ev)
                product["last_min"] = {"price": best.price, "url": best_offer["url"],
                                       "store": best_offer.get("label") or best_offer["store"]}
                if events and send and not args.health:
                    telegram_io.send_message(chat_id, "\n\n".join(events))

        # --- 3. Reports ---
        hour = datetime.now(UTC).hour
        want_digest = (args.digest or (hour == DIGEST_RUN_HOUR_UTC)) and not args.no_digest
        if send and not args.health:
            if first_run:
                telegram_io.send_message(
                    chat_id, f"✅ <b>Cenu Mednieks initialized</b> — tracking "
                             f"{len(products['products'])} products ({ok} ok, {failed} failed).")
            if want_digest and products["products"]:
                telegram_io.send_message(
                    chat_id, "\n".join(alerts.digest_lines(products, prices)))
        if args.health:
            summary = alerts.health_summary(products, prices)
            print(summary)
            if send and "⚠️" in summary:
                telegram_io.send_message(chat_id, summary)

        # --- 4. Persist (merged over anything another runner wrote meanwhile) ---
        if gist:
            store.merge_write(F_PRODUCTS, products, state_mod.merge_products)
            store.merge_write(F_PRICES, prices, state_mod.merge_prices)
            store.merge_write(F_BOT, bot_state, state_mod.merge_bot_state)
        else:
            store.write(F_PRODUCTS, products)
            store.write(F_PRICES, prices)
            store.write(F_BOT, bot_state)
    finally:
        if gist and have_lock:
            store.release_lock(runner_id)

    log.info("done: %d ok, %d failed", ok, failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
