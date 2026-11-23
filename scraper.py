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
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import alerts
import catalog
import fetch
import i18n
import parse
import state as state_mod
import stores
import telegram_io
import users as users_mod
from config import (
    ADD_CANDIDATES_MAX,
    DIGEST_RUN_HOUR_UTC,
    F_BOT,
    F_LOCK,
    F_PRICES,
    F_PRODUCTS,
    F_USERS,
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


def _fetch_html(url: str, adapter: stores.StoreAdapter) -> str:
    """Fetch raw HTML for an offer — the ladder scrape_offer and the guided-add preview share."""
    if SCRAPERAPI_KEY and adapter.dc_blocked:
        return fetch.fetch_scraperapi(url, SCRAPERAPI_KEY,
                                      render=adapter.render_api,
                                      country=adapter.api_country)
    if adapter.uses_playwright():
        return fetch.fetch_playwright(url, adapter.wait_selector)
    try:
        return fetch.fetch_requests(url)
    except fetch.FetchError:
        if adapter.strategy == "requests+playwright":
            log.info("%s: requests failed, trying playwright", urlparse(url).hostname or url)
            return fetch.fetch_playwright(url, adapter.wait_selector)
        raise


def _fetch_api(url: str, adapter: stores.StoreAdapter, usage: dict | None,
               why: str = "") -> str:
    """ScraperAPI fetch at ScraperAPI's rates (10 credits behind bot protection, 5 on
    Amazon, +10 rendered); every billed response lands in `usage` at its real cost."""
    if why:
        log.warning("%s: %s — retrying via ScraperAPI", urlparse(url).hostname or url, why)
    counted = usage
    on_credit = ((lambda cost: alerts.bump_usage(counted, credits=cost))
                 if counted is not None else None)
    return fetch.fetch_scraperapi(url, SCRAPERAPI_KEY, render=adapter.render_api,
                                  country=adapter.api_country, on_credit=on_credit)


def scrape_offer(url: str, adapter: stores.StoreAdapter, usage: dict | None = None):
    """Fetch + extract one offer → (error: str | None, extraction | None).

    Cloudflare-guarded aggregators go through ScraperAPI in CI (dc_blocked presets);
    everything else uses the requests → Playwright ladder, rescued by one plain
    ScraperAPI call when that ladder is blocked (api_fallback presets). Every
    billed ScraperAPI response is counted into bot_state.usage, in credits (Q38).
    """
    ident = urlparse(url).hostname or url
    try:
        if SCRAPERAPI_KEY and adapter.dc_blocked:
            return _extract_or_diag(ident, _fetch_api(url, adapter, usage), adapter)
        try:
            html_text = _fetch_html(url, adapter)
        except fetch.FetchError as e:
            if not (SCRAPERAPI_KEY and adapter.api_fallback):
                raise
            return _extract_or_diag(
                ident, _fetch_api(url, adapter, usage, why=str(e)), adapter)
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


def prune_pending_ops(bot_state: dict, max_age_hours: int = 24) -> int:
    """Drop expired confirmation ops (cf:<key> registry) — returns the removed count."""
    ops = bot_state.get("pending_ops") or {}
    if not ops:
        return 0
    cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    stale = [k for k, op in ops.items() if str(op.get("created") or "") < cutoff]
    for k in stale:
        del ops[k]
    if stale:
        bot_state["pending_ops"] = ops
    return len(stale)


def stage_add(chat_id, query: str, user: dict, bot_state: dict,
              search_mode: bool = False) -> None:
    """Guided /add (Q30) and /search (Q27): preview aggregator matches as buttons.

    Scrapes the discovery search pages right now (two pages behind bot protection
    — 20 credits at ScraperAPI's rate), groups the rows into distinct product
    candidates, and stores everything in bot_state.pending_adds — nothing is
    tracked until a button is tapped. search_mode only changes the wording
    (price-check first, track after).
    """
    found: dict[str, dict] = {}
    usage = bot_state.setdefault("usage", {})
    for offer in catalog.discovery_offers(query):
        adapter = stores.adapter_for(offer["url"])
        try:
            if SCRAPERAPI_KEY and adapter.dc_blocked:
                html_text = _fetch_api(offer["url"], adapter, usage)
            else:
                html_text = _fetch_html(offer["url"], adapter)
        except fetch.FetchError as e:
            log.warning("add-preview %s failed: %s", offer["store"], e)
            continue
        for c in adapter.candidates(html_text, limit=ADD_CANDIDATES_MAX):
            name = (c.get("name") or "").strip()
            if not name:
                continue
            g = found.setdefault(name, {"name": name,
                                        "min_price": float(c.get("min_price") or 0.0),
                                        "count": 0})
            g["count"] += int(c.get("count") or 1)
            g["min_price"] = min(g["min_price"], float(c.get("min_price") or 0.0))
    ranked = sorted(found.values(), key=lambda g: (-g["count"], g["min_price"]))
    ranked = ranked[:ADD_CANDIDATES_MAX]
    key = f"{catalog.slugify(query)[:20]}.{int(time.time()) % 100000}"
    bot_state.setdefault("pending_adds", {})[str(chat_id)] = {
        "key": key, "query": query, "candidates": ranked,
        "created": now_iso(), "added_by": user.get("chat_id")}
    if ranked:
        head = (f"🔎 Results for “{alerts.esc(query)}” — tap ⭐ Track to watch one:"
                if search_mode else
                f"🔎 Matches for “{alerts.esc(query)}” — tap the real product:")
        telegram_io.send_card(chat_id, head, telegram_io.kb_candidates(key, ranked,
                                                                      mode="search"
                                                                      if search_mode
                                                                      else "add"))
        log.info("add-preview %s: %d candidate(s) (%s)", query, len(ranked),
                 "search" if search_mode else "add")
    else:
        telegram_io.send_card(chat_id,
                              f"Couldn't preview “{alerts.esc(query)}” — search pages "
                              f"failed. Try again later, or /add_url a specific product page.",
                              telegram_io.kb_candidates(key, [],
                                                        mode="search" if search_mode
                                                        else "add"))


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
    ud = users_mod.ensure(store.read(F_USERS))
    first_run = not prices
    # expired confirmations (24 h) never pile up in bot_state
    prune_pending_ops(bot_state)

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

    def notify_one(u: dict, product_id: str, text: str, specs, keyboard=None) -> None:
        """Single-recipient send with quiet-hour parking (Q19) + i18n (Q16).

        specs: optional [(key, fields)] — when the user prefers lv, texts are
        re-rendered from i18n instead of the legacy English strings.
        """
        lang = (u.get("prefs") or {}).get("lang") or "en"
        if lang != "en" and specs:
            text = "\n\n".join(
                i18n.T(lang, key, **fields) for key, fields in specs)
        if alerts.hold_alert(ud, u, text, keyboard, bot_state):
            return
        if keyboard:
            telegram_io.send_card(u["chat_id"], text, keyboard)
        else:
            telegram_io.send_message(u["chat_id"], text)

    def notify(product_id: str, events: list[str], specs=None, keyboard=None) -> None:
        """Alert routing (Q3/Q4/Q18/Q19): per-user watchlist DMs with action buttons."""
        text = "\n\n".join(events)
        for u in alerts.recipients(ud, product_id):
            notify_one(u, product_id, text, specs or [], keyboard)

    try:
        # --- 1. Telegram commands (added products get scraped on this very run) ---
        if users_mod.owner(ud) is None and (TELEGRAM_CHAT_ID or bot_state.get("chat_id")):
            users_mod.migrate_owner(ud, TELEGRAM_CHAT_ID or bot_state.get("chat_id"))
        owner_u = users_mod.owner(ud)  # migrate_owner returns the users dict, not the record
        chat_id = owner_u.get("chat_id") if owner_u else None
        if not args.local and TELEGRAM_BOT_TOKEN:
            try:
                me = telegram_io.get_me()
                if me:
                    log.info("telegram bot: @%s (id %s)", me.get("username"), me.get("id"))
                telegram_io.register_commands(chat_id)  # phase 3: self-healing /setcommands
                updates = telegram_io.get_updates(int(bot_state.get("update_offset") or 0))
                log.info("telegram: %d pending update(s)", len(updates))
                if not updates and not chat_id and not args.local:
                    log.warning("no /start found — send /start to @%s in Telegram, then re-run",
                                (me or {}).get("username", "<unknown>"))
                for u in updates:
                    bot_state["update_offset"] = max(int(bot_state.get("update_offset") or 0),
                                                     u.get("update_id", 0))
                    try:
                        if u.get("callback_query"):
                            telegram_io.handle_callback(u["callback_query"],
                                                        products, prices, bot_state, ud)
                        else:
                            telegram_io.handle_update(u, products, prices, bot_state,
                                                      ud, add_stager=stage_add)
                    except Exception as e:  # noqa: BLE001 — one bad update ≠ dead run
                        log.warning("update processing failed: %s", e)
            except Exception as e:  # noqa: BLE001 — commands must not kill the run
                log.warning("command processing failed: %s", e)
            # --- 1b. Commands delivered by the Worker inside the dispatch payload ---
            event_path = os.environ.get("GITHUB_EVENT_PATH")
            if event_path and os.path.exists(event_path):
                try:
                    with open(event_path, encoding="utf-8") as fh:
                        event = json.load(fh)
                    for u in telegram_io.payload_updates(event):
                        try:
                            telegram_io.handle_update(u, products, prices, bot_state,
                                                      ud, add_stager=stage_add)
                        except Exception as e:  # noqa: BLE001
                            log.warning("payload update failed: %s", e)
                except (OSError, ValueError) as e:
                    log.warning("payload command processing failed: %s", e)
            chat_id = (users_mod.owner(ud) or {}).get("chat_id") or chat_id

        # --- 1c. Deliver alerts held over someone's quiet hours (Q19) ---
        send = bool(TELEGRAM_BOT_TOKEN) and not args.local
        if send and not args.health:
            try:
                for held_chat, items in alerts.flush_held(ud, bot_state).items():
                    telegram_io.send_message(held_chat, "🌙 <b>Overnight alerts</b>:")
                    for item in items:
                        kb = item.get("kb")
                        if kb:
                            telegram_io.send_card(held_chat, item.get("text") or "", kb)
                        else:
                            telegram_io.send_message(held_chat, item.get("text") or "")
            except Exception as e:  # noqa: BLE001 — held alerts must not kill the run
                log.warning("held-alert flush failed: %s", e)

        # --- 2. Scrape: product → offers; product-level events from the run's best ---
        ok = failed = 0
        only_stores = {s.strip().lower() for s in args.stores.split(",")} if args.stores else set()
        skip_stores = {s.strip().lower() for s in args.skip_stores.split(",")} if args.skip_stores else set()
        prev_host = None
        for product in products["products"]:
            if args.only and args.only.lower() not in f"{product['id']} {product['name']}".lower():
                continue
            results: list[tuple[dict, parse.Extraction | None]] = []
            last_seen: dict[str, float] = {}   # each scraped offer's price before this run
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
                err, ex = scrape_offer(url, adapter, bot_state.setdefault("usage", {}))
                if err is not None or ex is None or ex.price is None:
                    st["fails"] = int(st.get("fails") or 0) + 1
                    st["last_error"] = (err or "price not found on page")[:300]
                    failed += 1
                    log.warning("FAIL %s · %s (×%d): %s",
                                product["id"], offer["store"], st["fails"], err)
                    # alert at 3 consecutive fails, then re-nag every 5th so a dead
                    # offer never goes permanently quiet — ops noise, owner only
                    nag = (st["fails"] == FAIL_ALERT_AFTER
                           or (st["fails"] > FAIL_ALERT_AFTER
                               and (st["fails"] - FAIL_ALERT_AFTER) % 5 == 0))
                    if send and not args.health and nag and chat_id:
                        telegram_io.send_message(
                            chat_id, alerts.failure_alert(entry, st["fails"], err or "?"))
                    results.append((offer, None))
                else:
                    st["fails"] = 0
                    st.pop("last_error", None)
                    prev = {"price": st.get("price"), "old_price": st.get("old_price"),
                            "in_stock": st.get("in_stock")} if st.get("price") is not None else None
                    if prev is not None:
                        last_seen[url] = prev["price"]
                    if not args.health and send:
                        events = alerts.offer_events(entry, ex, prev)
                        if events:
                            notify(product["id"], events,
                                   specs=alerts.offer_event_specs(entry, ex, prev))
                    st.update(price=ex.price, old_price=ex.old_price, in_stock=ex.in_stock,
                              shop=ex.shop, offers=ex.offers, display=ex.display,
                              currency=ex.currency, converted=ex.converted, last_checked=now_iso())
                    if ex.image and not product.get("image"):
                        product["image"] = ex.image  # phase 2: harvest once, reuse everywhere
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

            # product-level: best price this run drives drop + all-time-low + target alerts
            best_offer, best = catalog.best_result(results)
            if best is not None and best_offer is not None and best.price is not None:
                best_price = best.price  # narrowed local: calls below invalidate member narrowing
                events = []
                specs = []
                # like for like: last_min is only the previous run's best, which a failed
                # or not-yet-scraped source (daily cadence) leaves dearer than reality
                prev_min = catalog.previous_best(results, last_seen) or product.get("last_min")
                ev = alerts.product_min_event(product, best_offer, best, prev_min)
                sp = alerts.product_min_spec(product, best_offer, best, prev_min)
                if ev:
                    events.append(ev)
                if sp:
                    specs.append(sp)
                ev = alerts.atl_update(product, best_offer, best)
                sp = alerts.atl_spec(product, best_offer, best)
                if ev:
                    events.append(ev)
                if sp:
                    specs.append(sp)
                product["last_min"] = {"price": best.price, "url": best_offer["url"],
                                       "store": best_offer.get("label") or best_offer["store"]}
                if events and send and not args.health:
                    # rich card (Q18): Where / Chart / Watch / Mute buttons
                    notify(product["id"], events, specs=specs,
                           keyboard=telegram_io.kb_alert(product["id"]))
                # target prices (Q21): personal pings when the best price ≤ a user's target
                if send and not args.health:
                    for u in users_mod.active(ud):
                        # target_status records the hit itself (no double alerts)
                        if users_mod.target_status(u, product["id"], best_price) != "new-low":
                            continue
                        u_target = users_mod.target_for(u, product["id"])
                        if u_target is None:
                            continue  # raced clear — nothing to compare
                        text = alerts.target_event(product, best_price, u_target,
                                                   best_offer.get("label") or best_offer["store"])
                        notify_one(u, product["id"], text,
                                   [alerts.target_spec(product, best_price, u_target,
                                                       best_offer.get("label")
                                                       or best_offer["store"])],
                                   telegram_io.kb_alert(product["id"]))

        # --- 3. Reports: per-user personal digests (Q20) + budget (Q38) ---
        hour = datetime.now(UTC).hour
        if send and not args.health:
            if first_run and chat_id:
                telegram_io.send_message(
                    chat_id, f"✅ <b>Cenu Mednieks initialized</b> — tracking "
                             f"{len(products['products'])} products ({ok} ok, {failed} failed).")
            all_ids = [p["id"] for p in products["products"]]
            for u in users_mod.active(ud):
                prefs = u.get("prefs") or {}
                if not prefs.get("digest") or args.no_digest or not products["products"]:
                    continue
                if not (args.digest
                        or prefs.get("digest_hour_utc", DIGEST_RUN_HOUR_UTC) == hour):
                    continue
                ids = users_mod.effective_ids(u, all_ids)
                if not ids:
                    continue
                lang = prefs.get("lang") or "en"
                lines = alerts.digest_lines(products, prices, ids, lang=lang)
                if u.get("role") == users_mod.ROLE_OWNER:
                    budget = alerts.budget_line(bot_state.get("usage") or {}, lang=lang)
                    if budget:
                        lines.append(budget)
                telegram_io.send_message(u["chat_id"], "\n".join(lines))
            warn = alerts.budget_warning(bot_state.setdefault("usage", {}))
            if warn and chat_id:
                telegram_io.send_message(chat_id, warn)
        if args.health:
            summary = alerts.health_summary(products, prices)
            print(summary)
            if send and "⚠️" in summary and chat_id:
                telegram_io.send_message(chat_id, summary)

        # --- 4. Persist (merged over anything another runner wrote meanwhile) ---
        if gist:
            store.merge_write(F_PRODUCTS, products, state_mod.merge_products)
            store.merge_write(F_PRICES, prices, state_mod.merge_prices)
            store.merge_write(F_BOT, bot_state, state_mod.merge_bot_state)
            store.merge_write(F_USERS, ud, users_mod.merge_users)
        else:
            store.write(F_PRODUCTS, products)
            store.write(F_PRICES, prices)
            store.write(F_BOT, bot_state)
            store.write(F_USERS, ud)
    finally:
        if gist and have_lock:
            store.release_lock(runner_id)

    log.info("done: %d ok, %d failed", ok, failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
