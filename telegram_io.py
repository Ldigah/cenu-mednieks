"""Telegram API wrapper + command processing (getUpdates is polled on each run)."""
from __future__ import annotations

import logging

import requests

import alerts
import catalog
import charts
import commands
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger("telegram")

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method: str, timeout: int = 25, **payload):
    r = requests.post(API.format(token=TELEGRAM_BOT_TOKEN, method=method),
                      json=payload, timeout=timeout)
    if not r.ok:
        log.error("telegram %s failed: %s", method, r.text[:300])
    return r.json() if r.ok else None


def send_message(chat_id, text: str):
    for chunk in _split(text, 3800):
        _call("sendMessage", chat_id=chat_id, text=chunk, parse_mode="HTML",
              disable_web_page_preview=True)


def send_photo(chat_id, png: bytes, caption: str = ""):
    try:
        r = requests.post(API.format(token=TELEGRAM_BOT_TOKEN, method="sendPhoto"),
                          data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                          files={"photo": ("chart.png", png, "image/png")}, timeout=60)
        if not r.ok:
            log.error("sendPhoto failed: %s", r.text[:300])
    except requests.RequestException as e:
        log.error("sendPhoto error: %s", e)


def get_me() -> dict | None:
    """Bot identity from getMe — used in logs so the owner can verify which bot is wired."""
    data = _call("getMe")
    return data.get("result") if data else None


def get_updates(last_update_id: int) -> list[dict]:
    params = {"timeout": 0, "limit": 100, "allowed_updates": "message"}
    if last_update_id:
        params["offset"] = last_update_id + 1
    r = requests.get(API.format(token=TELEGRAM_BOT_TOKEN, method="getUpdates"),
                     params=params, timeout=25)
    r.raise_for_status()
    return r.json().get("result", [])


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit and buf:
            out.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        out.append(buf)
    return out


HELP = (
    "🤖 <b>Cenu Mednieks</b> — price &amp; discount tracker\n\n"
    "Products are tracked across stores — /add a name once, I watch everywhere:\n\n"
    "/add &lt;name&gt; — track a product on salidzini.lv, kurpirkt.lv, ceno.lv\n"
    "/add &lt;url&gt; [name] — track one specific store page\n"
    "/add-url &lt;url&gt; [name-part] — attach another store to a product\n"
    "/list — tracked products\n"
    "/prices — best price per product\n"
    "/where &lt;name-part&gt; — all stores + prices for a product\n"
    "/stats [name-part] — price statistics (best, ATL, min7/30, avg30)\n"
    "/best — products ranked by discount depth vs 30-day average\n"
    "/threshold &lt;name-part&gt; &lt;pct&gt; — drop-alert threshold (default 5%)\n"
    "/chart [name-part] — price-history chart\n"
    "/remove &lt;name-part&gt; — stop tracking\n"
    "/check — re-scrape everything on this run\n\n"
    "Alerts: best-price drops ≥ threshold, all-time lows, new/deeper discounts, "
    "restocks. Price rises are recorded silently.\n"
    "💡 Commands are processed when the bot runs (3×/day) — for instant answers "
    "and instant /add, use the web app (bot menu button) or trigger "
    "Actions → <b>Price tracker</b> → <i>Run workflow</i>."
)


def handle_update(update: dict, products: dict, prices: dict, bot_state: dict) -> int | None:
    """Process one update. Replies are sent immediately; returns the chat id if active.

    Only the configured chat (TELEGRAM_CHAT_ID, or the first /start if unset) is served.
    """
    msg = update.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return None

    allowed = str(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else bot_state.get("chat_id")
    if allowed and str(chat_id) != str(allowed):
        log.info("ignoring message from unauthorized chat %s", chat_id)
        return None

    parsed = commands.parse_command(text)
    if parsed is None:
        return None
    cmd, arg = parsed

    if cmd in ("start", "help"):
        if not allowed:
            bot_state["chat_id"] = chat_id
            send_message(chat_id, "✅ This chat is now the bot's home.\n\n" + HELP)
        else:
            send_message(chat_id, HELP)
        return chat_id

    if not (TELEGRAM_CHAT_ID or bot_state.get("chat_id")):
        return None  # adopt only via /start

    if cmd == "list":
        if not products["products"]:
            send_message(chat_id, "Nothing tracked yet. /add &lt;name&gt; or /add &lt;url&gt;")
        else:
            lines = []
            for i, prod in enumerate(products["products"], 1):
                stores_txt = ", ".join(sorted({o["store"] for o in prod["offers"]}))
                lines.append(f"{i}. <b>{alerts.esc(prod['name'] or prod['id'])}</b> — "
                             f"{len(prod['offers'])} store(s): {alerts.esc(stores_txt)}")
            send_message(chat_id, "<b>Tracked products</b>\n" + "\n".join(lines))

    elif cmd == "prices":
        lines = ["💰 <b>Best prices now</b>"]
        for prod in products["products"]:
            offer, st = catalog.best_state(prod, prices)
            if offer is None or st is None:
                lines.append(f"• {alerts.esc(prod['name'] or prod['id'])}: no data yet")
                continue
            p = st.get("price")
            old = st.get("old_price")
            stock = st.get("in_stock")
            badge = "✅" if stock else ("❌" if stock is False else "•")
            extra = f" <i>(reg. {alerts.fmt_eur(old)})</i>" if old and p and old > p else ""
            conv = (f" <i>(≈ from {alerts.esc(st['display'])})</i>"
                    if st.get("converted") and st.get("display") else "")
            warn = (" ⚠️ non-EUR" if st.get("currency") not in (None, "EUR")
                    and not st.get("converted") else "")
            n = len(prod["offers"])
            lines.append(f"{badge} {alerts.esc(prod['name'] or prod['id'])}: "
                         f"<b>{alerts.fmt_eur(p)}</b> at "
                         f"{alerts.esc(offer.get('label') or offer['store'])}"
                         f"{extra}{conv}{warn} · {n} store{'s' if n != 1 else ''}")
        send_message(chat_id, "\n".join(lines))

    elif cmd == "where":
        matches = catalog.find_products(products, arg) if arg else products["products"]
        if not matches:
            send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
            return chat_id
        for prod in matches[:3]:  # keep the reply manageable
            lines = [f"🏬 <b>{alerts.esc(prod['name'] or prod['id'])}</b> — "
                     f"{len(prod['offers'])} store(s)"]
            for o in prod["offers"]:
                st = prices.get(o["url"], {}) or {}
                if st.get("fails"):
                    lines.append(f"• {alerts.esc(o['store'])}: ⚠️ failing ×{st['fails']}")
                elif st.get("price") is not None:
                    shop = f" ({alerts.esc(st['shop'])})" if st.get("shop") else ""
                    stock = "" if st.get("in_stock") is not False else " ❌out"
                    lines.append(f"• {alerts.esc(o['store'])}: <b>{alerts.fmt_eur(st['price'])}</b>"
                                 f"{shop}{stock}")
                else:
                    lines.append(f"• {alerts.esc(o['store'])}: not checked yet")
            send_message(chat_id, "\n".join(lines))

    elif cmd == "add":
        _cmd_add(chat_id, products, arg)

    elif cmd == "add-url":
        _cmd_add_url(chat_id, products, arg)

    elif cmd == "threshold":
        _cmd_threshold(chat_id, products, arg)

    elif cmd == "remove":
        if not arg:
            send_message(chat_id, "Usage: /remove &lt;name-part&gt;")
            return chat_id
        matches = catalog.find_products(products, arg)
        if not matches:
            send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
        elif len(matches) == 1:
            products["products"] = [p for p in products["products"] if p is not matches[0]]
            send_message(chat_id, f"🗑 Removed <b>{alerts.esc(matches[0]['name'])}</b> "
                                  f"({len(matches[0]['offers'])} stores) and its history.")
        else:
            listing = "\n".join(f"• {alerts.esc(p['name'] or p['id'])}" for p in matches)
            send_message(chat_id, f"Multiple matches — be more specific:\n{listing}")

    elif cmd == "chart":
        matches = catalog.find_products(products, arg) if arg else products["products"]
        if not matches:
            send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
            return chat_id
        sent = 0
        for prod in matches:
            if sent >= 3:
                send_message(chat_id, "(more charts omitted — narrow the name)")
                break
            png = charts.chart_product(prod["name"] or prod["id"], prod["offers"], prices,
                                       prod.get("atl"))
            if png:
                send_photo(chat_id, png,
                           caption=f"📈 {alerts.esc(prod['name'] or prod['id'])} — price history")
                sent += 1
        if sent == 0:
            send_message(chat_id, "No price history yet — charts appear after the first run.")

    elif cmd == "stats":
        matches = catalog.find_products(products, arg) if arg else products["products"]
        if not matches:
            send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
            return chat_id
        for prod in matches[:3]:
            s = catalog.product_stats(prod, prices)
            lines = [f"📊 <b>{alerts.esc(prod['name'] or prod['id'])}</b> "
                     f"({s['stores']} stores, {s['points']} observations)"]
            b = s["best"]
            if b["price"] is not None:
                lines.append(f"• Best now: <b>{alerts.fmt_eur(b['price'])}</b> at "
                             f"{alerts.esc(b['store'] or '?')}")
            atl = s["atl"]
            if atl and atl.get("price") is not None:
                lines.append(f"• All-time low: {alerts.fmt_eur(atl['price'])} "
                             f"({str(atl.get('date', ''))[:10]} at {alerts.esc(atl.get('store') or '?')})")
            if s["min7"] is not None:
                lines.append(f"• 7-day min: {alerts.fmt_eur(s['min7'])} · "
                             f"30-day min: {alerts.fmt_eur(s['min30'])}")
            if s["avg30"] is not None:
                lines.append(f"• 30-day avg: {alerts.fmt_eur(s['avg30'])}")
            send_message(chat_id, "\n".join(lines))

    elif cmd == "best":
        scored = []
        for prod in products["products"]:
            s = catalog.product_stats(prod, prices)
            bp, avg = s["best"]["price"], s["avg30"]
            if bp is not None and avg is not None and avg > 0:
                depth = (avg - bp) / avg * 100
                scored.append((depth, prod, s))
        if not scored:
            send_message(chat_id, "Not enough history yet — /best works after ~a day of runs.")
            return chat_id
        scored.sort(key=lambda x: x[0], reverse=True)
        lines = ["🔥 <b>Deepest current discounts</b> (vs 30-day average)"]
        for depth, prod, s in scored[:5]:
            atl = s["atl"]
            atl_txt = f" · ATL {alerts.fmt_eur(atl['price'])}" if atl and atl.get("price") else ""
            lines.append(f"• <b>{alerts.esc(prod['name'] or prod['id'])}</b>: "
                         f"<b>{alerts.fmt_eur(s['best']['price'])}</b> at "
                         f"{alerts.esc(s['best']['store'] or '?')} "
                         f"(−{depth:.1f}% vs avg){atl_txt}")
        send_message(chat_id, "\n".join(lines))

    elif cmd == "check":
        send_message(chat_id, "🔎 Re-scraping everything on this run — results arrive shortly.")

    else:
        send_message(chat_id, f"Unknown command /{alerts.esc(cmd)}. Try /help")

    return chat_id


def _cmd_add(chat_id, products: dict, arg: str) -> None:
    """/add <name> → product across aggregators; /add <url> [name] → single-store product."""
    if not arg:
        send_message(chat_id, "Usage: /add &lt;product-name&gt;  or  /add &lt;url&gt; [name]")
        return
    first = arg.split(maxsplit=1)[0]
    if catalog.looks_like_url(first):
        parts = arg.split(maxsplit=1)
        url = parts[0]
        name = parts[1].strip() if len(parts) > 1 else ""
        offer, note = catalog.make_offer(url)
        if offer is None:
            send_message(chat_id, f"❌ {alerts.esc(note)}")
            return
        prod_id = catalog.slugify(name or offer["store"])
        existing = next((p for p in products["products"] if p["id"] == prod_id), None)
        if existing is not None and catalog.add_offer(existing, offer):
            send_message(chat_id, f"➕ Added <b>{alerts.esc(offer['store'])}</b> to "
                                  f"<b>{alerts.esc(existing['name'])}</b> — checking it this run.{note}")
            return
        if any(o["url"] == offer["url"] for p in products["products"] for o in p["offers"]):
            send_message(chat_id, "Already tracking that URL.")
            return
        prod = catalog.make_product(name or offer["store"], [offer])
        products["products"].append(prod)
        send_message(chat_id, f"➕ Tracking <b>{alerts.esc(prod['name'])}</b> at "
                              f"{alerts.esc(offer['store'])} — checking it this run.{note}")
        return
    # product search mode
    query = arg.strip()
    if any(p["id"] == catalog.slugify(query) for p in products["products"]):
        send_message(chat_id, f"Already tracking <b>{alerts.esc(query)}</b>.")
        return
    prod = catalog.make_product(query, catalog.discovery_offers(query))
    stores_txt = ", ".join(sorted({o["store"] for o in prod["offers"]}))
    products["products"].append(prod)
    send_message(chat_id, f"➕ Tracking <b>{alerts.esc(prod['name'])}</b> across "
                          f"{alerts.esc(stores_txt)} — first results this run.\n"
                          f"Attach more stores (Amazon, direct shops) with "
                          f"/add-url &lt;url&gt; {alerts.esc(catalog.slugify(query))}.")


def _cmd_add_url(chat_id, products: dict, arg: str) -> None:
    """/add-url <url> [name-part] — attach a store offer to an existing (or new) product."""
    parts = arg.split(maxsplit=1)
    if not parts:
        send_message(chat_id, "Usage: /add-url &lt;product-url&gt; [name-part]")
        return
    url = parts[0]
    part = parts[1].strip() if len(parts) > 1 else ""
    offer, note = catalog.make_offer(url)
    if offer is None:
        send_message(chat_id, f"❌ {alerts.esc(note)}")
        return
    if any(o["url"] == offer["url"] for p in products["products"] for o in p["offers"]):
        send_message(chat_id, "Already tracking that URL.")
        return
    matches = catalog.find_products(products, part) if part else []
    if len(matches) == 1:
        target = matches[0]
        if catalog.add_offer(target, offer):
            send_message(chat_id, f"➕ Added <b>{alerts.esc(offer['store'])}</b> to "
                                  f"<b>{alerts.esc(target['name'])}</b> — checking it this run.{note}")
        else:
            send_message(chat_id, "Already tracking that URL.")
        return
    if len(matches) > 1:
        listing = "\n".join(f"• {alerts.esc(p['name'] or p['id'])}" for p in matches)
        send_message(chat_id, f"Multiple products match — be more specific:\n{listing}")
        return
    name = part or offer["store"]
    prod = catalog.make_product(name, [offer])
    products["products"].append(prod)
    send_message(chat_id, f"➕ Tracking <b>{alerts.esc(prod['name'])}</b> at "
                          f"{alerts.esc(offer['store'])} — checking it this run.{note}")


def _cmd_threshold(chat_id, products: dict, arg: str) -> None:
    """/threshold <name-part> <pct> — per-product drop-alert threshold."""
    parts = arg.rsplit(maxsplit=1)
    if len(parts) != 2:
        send_message(chat_id, "Usage: /threshold &lt;name-part&gt; &lt;pct&gt;  (e.g. /threshold jbl 10)")
        return
    part, pct_raw = parts[0].strip(), parts[1].strip().rstrip("%")
    try:
        pct = float(pct_raw)
    except ValueError:
        send_message(chat_id, f"“{alerts.esc(pct_raw)}” is not a percentage.")
        return
    if not 0.1 <= pct <= 90:
        send_message(chat_id, "Pick a threshold between 0.1% and 90%.")
        return
    matches = catalog.find_products(products, part)
    if not matches:
        send_message(chat_id, f"Nothing matches “{alerts.esc(part)}”.")
    elif len(matches) > 1:
        listing = "\n".join(f"• {alerts.esc(p['name'] or p['id'])}" for p in matches)
        send_message(chat_id, f"Multiple products match — be more specific:\n{listing}")
    else:
        matches[0]["threshold_pct"] = pct
        send_message(chat_id, f"🎚 <b>{alerts.esc(matches[0]['name'])}</b>: drop alerts now "
                              f"fire at −{pct:g}%.")
