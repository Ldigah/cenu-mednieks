"""Telegram API wrapper + command processing (getUpdates is polled on each run)."""
from __future__ import annotations

import logging

import requests

import alerts
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
    "Commands (processed when the bot runs — 3×/day at ~07/15/23 Latvia time):\n"
    "/list — tracked products\n"
    "/prices — latest known prices\n"
    "/add &lt;url&gt; [name] — start tracking a product\n"
    "/remove &lt;name-part&gt; — stop tracking\n"
    "/chart [name-part] — price-history chart\n"
    "/check — what I know right now\n\n"
    "💡 For instant command processing: GitHub → your repo → Actions → "
    "<b>Price tracker</b> → <i>Run workflow</i>."
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
            send_message(chat_id, "Nothing tracked yet. /add &lt;url&gt; [name]")
        else:
            lines = [f"{i}. {alerts.esc(commands.entry_label(e))}\n   {e['url']}"
                     for i, e in enumerate(products["products"], 1)]
            send_message(chat_id, "<b>Tracked products</b>\n" + "\n".join(lines))

    elif cmd == "prices":
        lines = ["💰 <b>Latest prices</b>"]
        for e in products["products"]:
            st = prices.get(e["url"], {})
            p = st.get("price")
            old = st.get("old_price")
            stock = st.get("in_stock")
            badge = "✅" if stock else ("❌" if stock is False else "•")
            extra = f" <i>(reg. {alerts.fmt_eur(old)})</i>" if old and p and old > p else ""
            conv = (f" <i>(≈ from {alerts.esc(st['display'])})</i>"
                    if st.get("converted") and st.get("display") else "")
            warn = (" ⚠️ non-EUR" if st.get("currency") not in (None, "EUR")
                    and not st.get("converted") else "")
            lines.append(f"{badge} {alerts.esc(commands.entry_label(e))}: "
                         f"<b>{alerts.fmt_eur(p)}</b>{extra}{conv}{warn}")
        send_message(chat_id, "\n".join(lines))

    elif cmd == "add":
        parts = arg.split(maxsplit=1)
        if not parts:
            send_message(chat_id, "Usage: /add &lt;product-url&gt; [name]")
            return chat_id
        url, name = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        if any(e["url"] == url for e in products["products"]):
            send_message(chat_id, "Already tracking that URL.")
            return chat_id
        entry, note = commands.make_entry(url, name)
        if entry is None:
            send_message(chat_id, f"❌ {alerts.esc(note)}")
            return chat_id
        if not entry["name"]:
            entry["name"] = entry["store_label"]
        products["products"].append(entry)
        send_message(chat_id, f"➕ Added <b>{alerts.esc(entry['name'])}</b> "
                              f"({alerts.esc(entry['store_label'])}) — checking it this run.{note}")

    elif cmd == "remove":
        if not arg:
            send_message(chat_id, "Usage: /remove &lt;name-part&gt;")
            return chat_id
        matches = commands.find_entries(products, arg)
        if not matches:
            send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
        elif len(matches) == 1:
            products["products"] = [e for e in products["products"] if e is not matches[0]]
            send_message(chat_id, f"🗑 Removed {alerts.esc(commands.entry_label(matches[0]))}")
        else:
            listing = "\n".join(f"• {alerts.esc(commands.entry_label(e))}" for e in matches)
            send_message(chat_id, f"Multiple matches — be more specific:\n{listing}")

    elif cmd == "chart":
        matches = commands.find_entries(products, arg) if arg else products["products"]
        if not matches:
            send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
            return chat_id
        groups: dict[str, list[dict]] = {}
        for e in matches:
            groups.setdefault(e["name"] or e["id"], []).append(e)
        sent = 0
        for name, entries in groups.items():
            if sent >= 3:
                send_message(chat_id, "(more charts omitted — narrow the name)")
                break
            png = charts.chart_product(name, entries, prices)
            if png:
                send_photo(chat_id, png, caption=f"📈 {alerts.esc(name)} — price history")
                sent += 1
        if sent == 0:
            send_message(chat_id, "No price history yet — charts appear after the first run.")

    elif cmd == "check":
        send_message(chat_id, "🔎 Re-scraping everything on this run — results arrive shortly.")

    else:
        send_message(chat_id, f"Unknown command /{alerts.esc(cmd)}. Try /help")

    return chat_id
