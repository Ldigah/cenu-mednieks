"""Telegram API wrapper + command/callback processing.

Multi-user (DECISIONS.md 2026-11-19, the 40 questions): approved users only —
active members get instant answers, mutations are quota/permission checked,
strangers get a friendly blurb + [Request access] and the owner approves with
one tap. Replies carry inline buttons where a tap beats typing (Q18/Q29/Q31).

Poll mode (no webhook): callback_query updates arrive via getUpdates and are
handled here directly. Webhook mode: the Worker answers/handles callbacks
itself or dispatches the command with confirmed=true (payload_updates).

Naming: the module `users` holds the logic; `ud` is the users.json data dict.
"""
from __future__ import annotations

import logging

import requests

import alerts
import catalog
import charts
import commands
import i18n
import users as users_mod
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


def send_card(chat_id, text: str, keyboard: list[list[dict]] | None = None):
    """One HTML message with optional inline buttons (callback data ≤64 bytes)."""
    if len(text) > 3800:
        send_message(chat_id, text)
        return
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                     "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    _call("sendMessage", **payload)


def send_photo(chat_id, png: bytes, caption: str = ""):
    try:
        r = requests.post(API.format(token=TELEGRAM_BOT_TOKEN, method="sendPhoto"),
                          data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                          files={"photo": ("chart.png", png, "image/png")}, timeout=60)
        if not r.ok:
            log.error("sendPhoto failed: %s", r.text[:300])
    except requests.RequestException as e:
        log.error("sendPhoto error: %s", e)


def answer_callback(cb_id, text: str = ""):
    _call("answerCallbackQuery", callback_query_id=cb_id, text=text[:190])


def get_me() -> dict | None:
    """Bot identity from getMe — used in logs so the owner can verify which bot is wired."""
    data = _call("getMe")
    return data.get("result") if data else None


def get_updates(last_update_id: int) -> list[dict]:
    params = {"timeout": 0, "limit": 100,
              "allowed_updates": '["message","callback_query"]'}
    if last_update_id:
        params["offset"] = last_update_id + 1
    r = requests.get(API.format(token=TELEGRAM_BOT_TOKEN, method="getUpdates"),
                     params=params, timeout=25)
    if r.status_code == 409:
        # A webhook (the Cloudflare Worker) is active — Telegram forbids getUpdates
        # while it's set. Commands arrive via repository_dispatch payloads instead.
        log.info("webhook active — getUpdates skipped (commands flow through the Worker)")
        return []
    r.raise_for_status()
    return r.json().get("result", [])


def payload_updates(event: dict) -> list[dict]:
    """Synthetic updates from a repository_dispatch client_payload.

    The Worker forwards commands as {commands: [{text, chat_id, confirmed?}]}:
    with a webhook active, Telegram forbids getUpdates (409), so CI cannot
    poll — it processes github.event.client_payload.commands directly.
    `confirmed` (set when the action came from an inline button) skips the
    confirmation step in mutating commands.
    """
    cmds = (event.get("client_payload") or {}).get("commands") or []
    out = []
    for i, cmd in enumerate(cmds):
        text = (((cmd or {}).get("text")) or "").strip()
        chat = (cmd or {}).get("chat_id")
        if text and chat:
            out.append({"update_id": -(i + 1),
                        "confirmed": bool((cmd or {}).get("confirmed")),
                        "message": {"message_id": 0, "date": 0,
                                    "chat": {"id": chat}, "text": text}})
    return out


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
    "🤖 <b>Cenu Mednieks</b> — price &amp; discount hunter\n\n"
    "One shared catalog, your own watchlist — watch a product once and I ping "
    "you when it gets cheaper.\n\n"
    "<b>Browse (instant)</b>\n"
    "/prices — best price per product (+ % above ATL)\n"
    "/best — deepest discounts vs 30-day average\n"
    "/where &lt;name-part&gt; — all stores + prices for a product\n"
    "/stats [name-part] — best, ATL, min7/30, avg30, volatility, target\n"
    "/list [#tag] — everything tracked (filter by tag)\n"
    "/tags — all tags\n"
    "/search &lt;name&gt; — price-check without tracking (5/day)\n\n"
    "<b>Track</b>\n"
    "/add &lt;name&gt; — I show matches, you pick (or /add &lt;url&gt; [name])\n"
    "/add_url &lt;url&gt; [name-part] — attach another store\n"
    "/watch [part] / /unwatch &lt;part&gt; — choose what pings you ⭐\n"
    "/mute &lt;part&gt; / /unmute &lt;part&gt; — silence one product\n"
    "/target &lt;part&gt; &lt;price|off&gt; — ping me when it's under X 🎯\n"
    "/threshold &lt;part&gt; &lt;pct&gt; — drop-alert threshold (default 5%)\n"
    "/tag &lt;part&gt; #tag / /untag &lt;part&gt; #tag — organize\n"
    "/remove &lt;part&gt; — stop tracking (asks to confirm)\n\n"
    "<b>You</b>\n"
    "/settings — digest, quiet hours, language\n"
    "/watchall — (owner) toggle hearing about everything\n"
    "/quota — how many products you can still add\n"
    "/whoami — your profile\n"
    "/requests — (owner) approve/decline access requests\n\n"
    "Alerts: best-price drops ≥ threshold, all-time lows, discounts, restocks, "
    "target hits. Rises are recorded silently. Quiet hours hold alerts until "
    "morning.\n"
    "💡 Instant answers here; /add and /chart results arrive with the next "
    "tracker run (~2–5 min when triggered from the app or the Worker)."
)

INTRO = (
    "👋 Hi! I'm <b>Cenu Mednieks</b> — I hunt prices &amp; discounts across "
    "Latvian, Baltic and EU shops.\n\n"
    "Access is by approval: tap the button and the owner will let you in. "
    "Once approved you can track products, build a watchlist and get deal alerts."
)

HELP_LV = (
    "🤖 <b>Cenu Mednieks</b> — cenu un atlaides mednieks\n\n"
    "Viens kopīgs katalogs, tavs saraksts — seko produktam un ziņošu, kad "
    "tas kļūst lētāks.\n\n"
    "<b>Skatīt (tūlīt)</b>\n"
    "/prices — labākā cena produktam (+ % virs minimuma)\n"
    "/best — dziļākās atlaides pret 30 dienu vidējo\n"
    "/where &lt;daļa&gt; — visi veikali + cenas produktam\n"
    "/stats [daļa] — labākā, minimums, min7/30, vidējā, nestabilitāte, mērķis\n"
    "/list [#tags] — visi produkti (filtrēt pēc tags)\n"
    "/tags — visi tags\n"
    "/search &lt;vārds&gt; — cenu pārbaude bez sekošanas (5/dienā)\n\n"
    "<b>Sekot</b>\n"
    "/add &lt;vārds&gt; — parādu atsākumus, tu izvēlies (vai /add &lt;url&gt; [nosaukums])\n"
    "/add_url &lt;url&gt; [daļa] — pievienot citu veikalu\n"
    "/watch [daļa] / /unwatch &lt;daļa&gt; — kas tevi ziņo ⭐\n"
    "/mute &lt;daļa&gt; / /unmute &lt;daļa&gt; — apklusināt produktu\n"
    "/target &lt;daļa&gt; &lt;cena|off&gt; — ziņot, kad cena ≤ X 🎯\n"
    "/threshold &lt;daļa&gt; &lt;pct&gt; — krituma slieksnis (noklusējums 5%)\n"
    "/tag &lt;daļa&gt; #tags / /untag &lt;daļa&gt; #tags — kārtot\n"
    "/remove &lt;daļa&gt; — beigt sekošanu (prasa apstiprinājumu)\n\n"
    "<b>Tu</b>\n"
    "/settings — kopsavilkums, klusās stundas, valoda\n"
    "/watchall — (īpašnieks) dzirdēt par visu\n"
    "/quota — cik produktu vari vēl pievienot\n"
    "/whoami — tavs profils\n"
    "/requests — (īpašnieks) piekļuves pieprasījumi\n\n"
    "Paziņojumi: cenu kritumi ≥ sliekšņa, visu laiku zemākās cenas, atlaides, "
    "atgriešanās noliktavā, mērķa trāpījumi. Kāpumi fiksējas klusi. Klusās "
    "stundas tur paziņojumus līdz rītam.\n"
    "💡 Atbildes šeit tūlīt; /add un /chart rezultāti nāk ar nākamo palaišanu "
    "(~2–5 min no app vai Worker)."
)


def tr(user: dict, key: str, **fields) -> str:
    """Localized user message (phase 3): per-user language, i18n fallback to en."""
    lang = (user.get("prefs") or {}).get("lang") or "en"
    return i18n.T(lang, key, **fields)


def register_commands(owner_chat_id=None) -> None:
    """Self-healing /setcommands equivalent (setMyCommands) — idempotent, called
    every run so a recreated bot or new chat scope always has the list.

    Everyone gets the member set; the owner's chat additionally gets the
    owner-only commands via a chat scope.
    """
    member = [
        ("start", "Open the bot / main menu"),
        ("add", "Track a product by name (guided pick) or URL"),
        ("add_url", "Attach another store URL to a tracked product"),
        ("watch", "Show your watchlist or watch a product"),
        ("unwatch", "Stop watching a product"),
        ("mute", "Silence alerts for one product"),
        ("unmute", "Un-silence a product"),
        ("target", "Ping me when price drops under X"),
        ("threshold", "Set drop-alert threshold % for a product"),
        ("tag", "Add a #tag to a product"),
        ("untag", "Remove a #tag from a product"),
        ("tags", "Show all tags"),
        ("search", "Price-check without tracking (5/day)"),
        ("list", "Show tracked products (optionally #tag)"),
        ("prices", "Best price per product with % above ATL"),
        ("where", "All stores and prices for one product"),
        ("stats", "Best, ATL, min/avg, volatility, your target"),
        ("best", "Deepest current discounts vs 30-day average"),
        ("chart", "Price history chart for a product"),
        ("remove", "Stop tracking a product (confirm)"),
        ("settings", "Digest, quiet hours and language"),
        ("quota", "Your remaining product quota"),
        ("whoami", "Your profile"),
        ("help", "How to use the bot"),
    ]
    owner_only = [
        ("requests", "Review access requests (owner)"),
        ("watchall", "Toggle alerts for everything (owner)"),
        ("check", "Re-scrape everything now (owner)"),
        ("ping", "Worker self-test (owner)"),
    ]
    to_kb = lambda cs: [{"command": c, "description": d} for c, d in cs]  # noqa: E731
    _call("setMyCommands", commands=to_kb(member))
    if owner_chat_id:
        _call("setMyCommands", commands=to_kb(member + owner_only),
              scope={"type": "chat", "chat_id": int(owner_chat_id)})

# ---------------------------------------------------------------------------
# Inline keyboards (pure — callback data ≤ 64 bytes)
# ---------------------------------------------------------------------------

def kb_alert(slug: str) -> list[list[dict]]:
    """Buttons on alert cards (Q18): act without typing."""
    return [[
        {"text": "🏬 Where", "callback_data": f"wh:{slug}"},
        {"text": "📈 Chart", "callback_data": f"ch:{slug}"},
    ], [
        {"text": "⭐ Watch", "callback_data": f"wt:{slug}"},
        {"text": "🔕 Mute", "callback_data": f"mu:{slug}"},
    ]]


def kb_confirm_remove(slug: str) -> list[list[dict]]:
    return [[
        {"text": "🗑 Yes, remove", "callback_data": f"ok:{slug}"},
        {"text": "Cancel", "callback_data": "noop"},
    ]]


def kb_confirm_op(key: str, yes: str = "✅ Confirm") -> list[list[dict]]:
    """Short-key confirmation (phase 2): a mutating op travels as cf:<6-hex key> —
    always within Telegram's 64-byte callback limit regardless of product id length."""
    return [[
        {"text": yes, "callback_data": f"cf:{key}"},
        {"text": "Cancel", "callback_data": "noop"},
    ]]


def ask_confirm(chat_id, bot_state: dict, command_text: str, prompt: str,
                yes: str = "✅ Confirm") -> None:
    """Register a pending op and send its confirm card (Q31).

    The op dispatches command_text with confirmed=true when the cf:<key> button
    is tapped — by the Worker (webhook mode) or handle_callback (poll mode).
    """
    import secrets
    key = secrets.token_hex(3)
    bot_state.setdefault("pending_ops", {})[key] = {
        "cmd": command_text, "chat_id": chat_id,
        "created": datetime_now_iso()}
    send_card(chat_id, prompt, kb_confirm_op(key, yes))


def datetime_now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds")


def kb_confirm_check() -> list[list[dict]]:
    return [[
        {"text": "🔎 Yes, re-scrape now", "callback_data": "chk"},
        {"text": "Cancel", "callback_data": "noop"},
    ]]


def kb_candidates(key: str, candidates: list[dict], mode: str = "add") -> list[list[dict]]:
    """Guided /add and /search (Q30/Q27): pick from matches, or take the raw query."""
    icon, bare = ("➕", "Add as-is (search URLs)") if mode == "add" \
        else ("⭐", "Track as-is (search URLs)")
    kb = [[{"text": f"{icon} {c['name'][:46]} — {c['min_price']:.2f} € ({c['count']})",
            "callback_data": f"add:{i}:{key}"}]
          for i, c in enumerate(candidates)]
    kb.append([{"text": bare, "callback_data": f"add:-1:{key}"}])
    kb.append([{"text": "Cancel", "callback_data": "noop"}])
    return kb


def kb_request() -> list[list[dict]]:
    return [[{"text": "🙋 Request access", "callback_data": "req"}]]


def kb_requests_row(chat_id: int) -> list[list[dict]]:
    return [[
        {"text": "✅ Approve", "callback_data": f"apr:{chat_id}"},
        {"text": "❌ Decline", "callback_data": f"dec:{chat_id}"},
    ]]


def kb_settings(user: dict) -> list[list[dict]]:
    prefs = user.get("prefs") or {}
    quiet = prefs.get("quiet") or "off"
    return [
        [{"text": f"📰 Digest: {'on' if prefs.get('digest') else 'off'}",
          "callback_data": "st:digest"}],
        [{"text": f"🕐 Digest hour: {prefs.get('digest_hour_utc')} UTC",
          "callback_data": "st:hour"}],
        [{"text": f"🌙 Quiet hours: {quiet}",
          "callback_data": "st:quiet"}],
        [{"text": f"🌐 Language: {prefs.get('lang') or 'en'}",
          "callback_data": "st:lang"}],
    ]


# ---------------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------------

def handle_update(update: dict, products: dict, prices: dict, bot_state: dict,
                  ud: dict | None = None, add_stager=None) -> int | None:
    """Process one message update. Replies are sent immediately; returns the chat id.

    ud: shared users.json dict — must already be normalized (users_mod.ensure by
    the run); this handler mutates it in place so the run persists the changes.
    add_stager: optional callable(chat_id, query, user, bot_state) implementing
    the guided-add search (scraper.py) — None falls back to instant tracking.
    """
    ud = ud if isinstance(ud, dict) else {"users": []}
    msg = update.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return None

    confirmed = bool(update.get("confirmed"))
    parsed = commands.parse_command(text)
    user = users_mod.get(ud, chat_id)

    # --- bootstrap: no owner anywhere → first /start adopts the chat ---
    no_owner = users_mod.owner(ud) is None and not (TELEGRAM_CHAT_ID or bot_state.get("chat_id"))
    if no_owner and parsed is not None and parsed[0] in ("start", "help"):
        from_user = msg.get("from") or {}
        users_mod.migrate_owner(ud, chat_id, from_user.get("first_name") or "",
                                from_user.get("username") or "")
        send_message(chat_id, "✅ This chat is now the bot's home.\n\n" + HELP)
        return chat_id
    if no_owner:
        return None  # legacy: only /start adopts

    if user is None or user.get("status") != users_mod.ST_ACTIVE:
        return _handle_stranger(ud, chat_id, msg, user)

    if parsed is None:
        return None
    cmd, arg = parsed
    is_owner = user.get("role") == users_mod.ROLE_OWNER

    if cmd in ("start", "help"):
        lang = (user.get("prefs") or {}).get("lang") or "en"
        send_message(chat_id, HELP_LV if lang == "lv" else HELP)
        return chat_id

    if cmd == "whoami":
        q = users_mod.quota_left(user, products)
        p = user.get("prefs") or {}
        send_message(chat_id, "\n".join([
            f"👤 <b>{alerts.esc(users_mod.fmt_user(user))}</b> — {user['role']}",
            f"• Watchlist: {len(user.get('watchlist') or [])} product(s)"
            + (" + everything" if user.get("watch_all") else ""),
            f"• Muted: {len(user.get('muted') or [])}",
            "• Quota: unlimited" if q is None else f"• Quota: {q} left",
            f"• Digest: {'on' if p.get('digest') else 'off'} at "
            f"{p.get('digest_hour_utc')} UTC · quiet: {p.get('quiet') or 'off'}",
        ]))
        return chat_id

    if cmd == "settings":
        _send_settings(chat_id, user)
        return chat_id

    if cmd == "requests":
        if not is_owner:
            send_message(chat_id, "⛔ Owner only.")
            return chat_id
        pend = users_mod.pending(ud)
        if not pend:
            send_message(chat_id, "No pending access requests.")
        for u in pend:
            send_card(chat_id,
                      f"🙋 <b>{alerts.esc(users_mod.fmt_user(u))}</b> requests access\n"
                      f"joined {str(u.get('added', ''))[:10]}",
                      kb_requests_row(u["chat_id"]))
        return chat_id

    if cmd == "watchall":
        if not is_owner:
            send_message(chat_id, "⛔ Owner only.")
            return chat_id
        user["watch_all"] = not user.get("watch_all")
        state = "everything" if user["watch_all"] else "watchlist only"
        send_message(chat_id, f"📢 Alerts: you now hear about <b>{state}</b>.")
        return chat_id

    if cmd == "quota":
        q = users_mod.quota_left(user, products)
        send_message(chat_id, tr(user, "quota_none") if q is None
                     else tr(user, "quota_left", q=q))
        return chat_id

    if cmd == "list":
        _cmd_list(chat_id, products, arg)
        return chat_id

    if cmd == "tags":
        idx = catalog.tag_index(products)
        if not idx:
            send_message(chat_id, "No tags yet — /tag &lt;name-part&gt; #tag to add one.")
        else:
            lines = [f"• #{t} ×{n}" for t, n in idx.items()]
            send_message(chat_id, "🏷 <b>Tags</b>\n" + "\n".join(lines))
        return chat_id

    if cmd == "tag":
        _cmd_tag(chat_id, products, user, ud, arg, on=True)
        return chat_id

    if cmd == "untag":
        _cmd_tag(chat_id, products, user, ud, arg, on=False)
        return chat_id

    if cmd == "target":
        _cmd_target(chat_id, products, prices, bot_state, user, ud, arg, confirmed)
        return chat_id

    if cmd == "search":
        _cmd_search(chat_id, products, bot_state, user, arg, add_stager)
        return chat_id

    if cmd == "prices":
        _cmd_prices(chat_id, products, prices)
        return chat_id

    if cmd == "where":
        _cmd_where(chat_id, products, prices, arg)
        return chat_id

    if cmd == "add":
        _cmd_add(chat_id, products, bot_state, user, ud, arg, confirmed, add_stager)
        return chat_id

    if cmd == "pick":
        _cmd_pick(chat_id, products, bot_state, user, ud, arg, confirmed)
        return chat_id

    if cmd == "add_url":
        _cmd_add_url(chat_id, products, user, ud, arg)
        return chat_id

    if cmd == "threshold":
        _cmd_threshold(chat_id, products, bot_state, user, ud, arg, confirmed)
        return chat_id

    if cmd == "watch":
        _cmd_watch(chat_id, products, user, arg)
        return chat_id

    if cmd == "unwatch":
        _cmd_unwatch(chat_id, products, user, arg)
        return chat_id

    if cmd == "mute":
        _cmd_mute(chat_id, products, user, arg, on=True)
        return chat_id

    if cmd == "unmute":
        _cmd_mute(chat_id, products, user, arg, on=False)
        return chat_id

    if cmd == "remove":
        _cmd_remove(chat_id, products, user, ud, arg, confirmed)
        return chat_id

    if cmd == "chart":
        _cmd_chart(chat_id, products, prices, arg)
        return chat_id

    if cmd == "stats":
        _cmd_stats(chat_id, products, prices, arg, user)
        return chat_id

    if cmd == "best":
        _cmd_best(chat_id, products, prices)
        return chat_id

    if cmd == "check":
        if is_owner:
            if confirmed:
                send_message(chat_id, "🔎 Re-scraping everything on this run.")
            else:
                send_card(chat_id,
                          "🔎 Re-scrape every product now? Costs Actions minutes "
                          "+ ScraperAPI credits.", kb_confirm_check())
        else:
            send_message(chat_id, "⛔ Owner only — /check triggers a full paid run.")
        return chat_id

    send_message(chat_id, f"Unknown command /{alerts.esc(cmd)}. Try /help")
    return chat_id


def _handle_stranger(ud: dict, chat_id, msg: dict, user: dict | None):
    """Q1/Q35: approved users only — friendly intro + one-tap request."""
    if user is not None and user.get("status") == users_mod.ST_PENDING:
        send_message(chat_id, "⏳ Your access request is waiting for the owner's "
                              "approval — you'll get a message here once approved.")
        return chat_id
    if user is not None and user.get("status") == users_mod.ST_BLOCKED:
        return None  # silently ignore blocked users
    from_user = msg.get("from") or {}
    created = users_mod.request_access(ud, chat_id,
                                       from_user.get("first_name") or "",
                                       from_user.get("username") or "")
    send_card(chat_id, INTRO, kb_request())
    owner_u = users_mod.owner(ud)
    if created and owner_u is not None:
        u = users_mod.get(ud, chat_id)
        if u is not None:  # just created above — this always holds, mypy needs it
            send_card(owner_u["chat_id"],
                      f"🙋 <b>{alerts.esc(users_mod.fmt_user(u))}</b> requests access.",
                      kb_requests_row(int(chat_id)))
    return chat_id


def _can_touch(user: dict, prod: dict) -> bool:
    """Owner, or the member who added the product (None added_by = pre-multi-user)."""
    if user.get("role") == users_mod.ROLE_OWNER:
        return True
    return prod.get("added_by") in (None, user.get("chat_id"))


def _find_one(chat_id, products: dict, part: str) -> dict | None:
    matches = catalog.find_products(products, part) if part else []
    if not matches:
        send_message(chat_id, f"Nothing matches “{alerts.esc(part)}”.")
        return None
    if len(matches) > 1:
        listing = "\n".join(f"• {alerts.esc(p['name'] or p['id'])}" for p in matches[:10])
        send_message(chat_id, f"Multiple matches — be more specific:\n{listing}")
        return None
    return matches[0]


# --- read-only commands -----------------------------------------------------

def _cmd_list(chat_id, products: dict, arg: str = "") -> None:
    if arg.strip().startswith("#"):
        shown = catalog.find_tagged(products, arg)
        if not shown:
            send_message(chat_id, f"Nothing tagged {alerts.esc(arg.strip())}. "
                                  "/tag &lt;name-part&gt; #tag to add one.")
            return
    else:
        shown = products["products"]
    if not shown:
        send_message(chat_id, "Nothing tracked yet. /add &lt;name&gt; or /add &lt;url&gt;")
        return
    lines = []
    for i, prod in enumerate(shown, 1):
        stores_txt = ", ".join(sorted({o["store"] for o in prod["offers"]}))
        tags_txt = " ".join(f"#{t}" for t in prod.get("tags") or [])
        lines.append(f"{i}. <b>{alerts.esc(prod['name'] or prod['id'])}</b> — "
                     f"{len(prod['offers'])} store(s): {alerts.esc(stores_txt)}"
                     + (f"  {alerts.esc(tags_txt)}" if tags_txt else ""))
    head = f"<b>Tracked products</b> {alerts.esc(arg.strip())}\n" \
        if arg.strip().startswith("#") else "<b>Tracked products</b>\n"
    send_message(chat_id, head + "\n".join(lines))


def _atl_signal(prod: dict, price: float | None) -> str:
    """Q22: '% above all-time low' — the buy signal, shown everywhere."""
    atl = prod.get("atl")
    if not atl or atl.get("price") is None or price is None or price <= 0:
        return ""
    if price <= atl["price"]:
        return " · 🏆 ATL"
    pct = (price - atl["price"]) / atl["price"] * 100
    return f" · +{pct:.0f}% above ATL"


def _cmd_prices(chat_id, products: dict, prices: dict) -> None:
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
                     f"{extra}{conv}{warn}{_atl_signal(prod, p)} "
                     f"· {n} store{'s' if n != 1 else ''}")
    send_message(chat_id, "\n".join(lines))


def _cmd_where(chat_id, products: dict, prices: dict, arg: str) -> None:
    matches = catalog.find_products(products, arg) if arg else products["products"]
    if not matches:
        send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
        return
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


DOW_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _cmd_stats(chat_id, products: dict, prices: dict, arg: str, user: dict | None = None) -> None:
    matches = catalog.find_products(products, arg) if arg else products["products"]
    if not matches:
        send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
        return
    for prod in matches[:3]:
        s = catalog.product_stats(prod, prices)
        lines = [f"📊 <b>{alerts.esc(prod['name'] or prod['id'])}</b> "
                 f"({s['stores']} stores, {s['points']} observations)"]
        b = s["best"]
        if b["price"] is not None:
            lines.append(f"• Best now: <b>{alerts.fmt_eur(b['price'])}</b> at "
                         f"{alerts.esc(b['store'] or '?')}{_atl_signal(prod, b['price'])}")
        atl = s["atl"]
        if atl and atl.get("price") is not None:
            lines.append(f"• All-time low: {alerts.fmt_eur(atl['price'])} "
                         f"({str(atl.get('date', ''))[:10]} at {alerts.esc(atl.get('store') or '?')})")
        if s["min7"] is not None:
            lines.append(f"• 7-day min: {alerts.fmt_eur(s['min7'])} · "
                         f"30-day min: {alerts.fmt_eur(s['min30'])}")
        if s["avg30"] is not None:
            lines.append(f"• 30-day avg: {alerts.fmt_eur(s['avg30'])}")
        # phase 2 buy signals (Q23)
        signals = []
        if s.get("vol") is not None:
            signals.append(f"volatility ±{s['vol']:.1f}%")
        if s.get("drops30") is not None:
            signals.append(f"{s['drops30']} drop(s) ≥5%/30d")
        if s.get("dow") is not None:
            signals.append(f"cheapest day {DOW_NAMES[s['dow']['day']]} "
                           f"(avg {alerts.fmt_eur(s['dow']['avg'])})")
        if signals:
            lines.append("• " + " · ".join(signals))
        # the caller's target (Q21)
        if user is not None:
            target = users_mod.target_for(user, prod["id"])
            if target is not None:
                bp = b["price"]
                if bp is not None and bp <= target:
                    lines.append(f"• 🎯 Target {alerts.fmt_eur(target)} — <b>hit!</b>")
                elif bp is not None:
                    away = (bp - target) / target * 100
                    lines.append(f"• 🎯 Target {alerts.fmt_eur(target)} — "
                                 f"{away:.0f}% away")
                else:
                    lines.append(f"• 🎯 Target {alerts.fmt_eur(target)}")
        send_message(chat_id, "\n".join(lines))


def _cmd_best(chat_id, products: dict, prices: dict) -> None:
    scored = []
    for prod in products["products"]:
        s = catalog.product_stats(prod, prices)
        bp, avg = s["best"]["price"], s["avg30"]
        if bp is not None and avg is not None and avg > 0:
            depth = (avg - bp) / avg * 100
            scored.append((depth, prod, s))
    if not scored:
        send_message(chat_id, "Not enough history yet — /best works after ~a day of runs.")
        return
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


def _cmd_chart(chat_id, products: dict, prices: dict, arg: str) -> None:
    matches = catalog.find_products(products, arg) if arg else products["products"]
    if not matches:
        send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
        return
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


# --- mutating commands ------------------------------------------------------

def _quota_ok(chat_id, user: dict, products: dict) -> bool:
    q = users_mod.quota_left(user, products)
    if q is not None and q <= 0:
        send_message(chat_id, f"📦 Quota reached ({user.get('quota')} products) — "
                              "ask the owner to raise it.")
        return False
    return True


def _create_search_product(chat_id, products: dict, user: dict, query: str) -> None:
    prod = catalog.make_product(query, catalog.discovery_offers(query))
    prod["added_by"] = user.get("chat_id")
    products["products"].append(prod)
    users_mod.watch(user, prod["id"])  # auto-watch what you add
    stores_txt = ", ".join(sorted({o["store"] for o in prod["offers"]}))
    send_message(chat_id, f"➕ Tracking <b>{alerts.esc(prod['name'])}</b> across "
                          f"{alerts.esc(stores_txt)} — first results this run.\n"
                          f"Attach more stores (Amazon, direct shops) with "
                          f"/add_url &lt;url&gt; {alerts.esc(prod['id'])}.")


def _cmd_add(chat_id, products: dict, bot_state: dict, user: dict, ud: dict,
             arg: str, confirmed: bool, add_stager) -> None:
    """/add <name> → guided pick (Q30); /add <url> [name] → single-store product."""
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
        if not _quota_ok(chat_id, user, products):
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
        prod["added_by"] = user.get("chat_id")
        products["products"].append(prod)
        users_mod.watch(user, prod["id"])
        send_message(chat_id, f"➕ Tracking <b>{alerts.esc(prod['name'])}</b> at "
                              f"{alerts.esc(offer['store'])} — checking it this run.{note}")
        return
    # product search mode → guided: show candidates, create only on pick (Q30)
    query = arg.strip()
    if any(p["id"] == catalog.slugify(query) for p in products["products"]):
        send_message(chat_id, f"Already tracking <b>{alerts.esc(query)}</b>.")
        return
    if not _quota_ok(chat_id, user, products):
        return
    if add_stager is not None:
        add_stager(chat_id, query, user, bot_state)
        return
    # no stager wired (local dry runs): legacy instant behavior
    _create_search_product(chat_id, products, user, query)


def _cmd_pick(chat_id, products: dict, bot_state: dict, user: dict, ud: dict,
              arg: str, confirmed: bool) -> None:
    """/pick <key> <i> — internal: create a product from a guided-add candidate."""
    parts = arg.split()
    if len(parts) != 2:
        send_message(chat_id, "Usage: /pick &lt;key&gt; &lt;index&gt;")
        return
    key, idx_raw = parts[0], parts[1]
    pend = (bot_state.get("pending_adds") or {}).get(str(chat_id)) or {}
    if not confirmed or pend.get("key") != key:
        send_message(chat_id, "That pick has expired — run /add again.")
        return
    candidates = pend.get("candidates") or []
    try:
        idx = int(idx_raw)
    except ValueError:
        idx = -1
    query = pend.get("query") or ""
    bot_state.setdefault("pending_adds", {}).pop(str(chat_id), None)
    if 0 <= idx < len(candidates):
        _create_search_product(chat_id, products, user, candidates[idx]["name"])
        return
    if idx == -1 and query:
        _create_search_product(chat_id, products, user, query)
        return
    send_message(chat_id, "Couldn't create that product — try /add again.")


def _cmd_add_url(chat_id, products: dict, user: dict, ud: dict, arg: str) -> None:
    """/add_url <url> [name-part] — attach a store offer to an existing (or new) product."""
    parts = arg.split(maxsplit=1)
    if not parts:
        send_message(chat_id, "Usage: /add_url &lt;product-url&gt; [name-part]")
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
    if not _quota_ok(chat_id, user, products):
        return
    name = part or offer["store"]
    prod = catalog.make_product(name, [offer])
    prod["added_by"] = user.get("chat_id")
    products["products"].append(prod)
    users_mod.watch(user, prod["id"])
    send_message(chat_id, f"➕ Tracking <b>{alerts.esc(prod['name'])}</b> at "
                          f"{alerts.esc(offer['store'])} — checking it this run.{note}")


def _cmd_threshold(chat_id, products: dict, bot_state: dict, user: dict, ud: dict,
                   arg: str, confirmed: bool) -> None:
    """/threshold <name-part> <pct> — per-product drop-alert threshold (Q31: confirm)."""
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
        return
    if len(matches) > 1:
        listing = "\n".join(f"• {alerts.esc(p['name'] or p['id'])}" for p in matches)
        send_message(chat_id, f"Multiple products match — be more specific:\n{listing}")
        return
    prod = matches[0]
    if not _can_touch(user, prod):
        send_message(chat_id, "⛔ Only the owner or whoever added this product can change it.")
        return
    if not confirmed:
        ask_confirm(chat_id, bot_state,
                    f"/threshold {prod['id']} {pct:g}",
                    f"🎚 Set drop threshold for <b>{alerts.esc(prod['name'])}</b> "
                    f"to −{pct:g}%?", f"✅ Set −{pct:g}%")
        return
    prod["threshold_pct"] = pct
    send_message(chat_id, tr(user, "threshold_set",
                             name=alerts.esc(prod["name"]), pct=f"{pct:g}"))


def _cmd_target(chat_id, products: dict, prices: dict, bot_state: dict, user: dict,
                ud: dict, arg: str, confirmed: bool) -> None:
    """/target <name-part> <price|off> — ping me when the best price ≤ X (Q21)."""
    parts = arg.rsplit(maxsplit=1)
    if len(parts) != 2:
        send_message(chat_id, "Usage: /target &lt;name-part&gt; &lt;price|off&gt;  "
                              "(e.g. /target jbl 65 or /target jbl off)")
        return
    part, price_raw = parts[0].strip(), parts[1].strip().replace("€", "").strip()
    matches = catalog.find_products(products, part)
    if not matches:
        send_message(chat_id, f"Nothing matches “{alerts.esc(part)}”.")
        return
    if len(matches) > 1:
        listing = "\n".join(f"• {alerts.esc(p['name'] or p['id'])}" for p in matches)
        send_message(chat_id, f"Multiple matches — be more specific:\n{listing}")
        return
    prod = matches[0]
    if price_raw.lower() in ("off", "none", "0", "clear"):
        if users_mod.clear_target(user, prod["id"]):
            send_message(chat_id, tr(user, "target_cleared",
                                     name=alerts.esc(prod["name"])))
        else:
            send_message(chat_id, tr(user, "no_target"))
        return
    price = commands.parse_price_arg(price_raw)
    if price is None or not 0.5 <= price <= 100000:
        send_message(chat_id, f"“{alerts.esc(price_raw)}” is not a price in EUR "
                              "(0.5–100 000).")
        return
    current = users_mod.target_for(user, prod["id"])
    if current is not None and abs(current - price) > 0.005 and not confirmed:
        ask_confirm(chat_id, bot_state,
                    f"/target {prod['id']} {price:g}",
                    f"🎯 Change your target for <b>{alerts.esc(prod['name'])}</b> from "
                    f"{alerts.fmt_eur(current)} to <b>{alerts.fmt_eur(price)}</b>?",
                    f"✅ Set {alerts.fmt_eur(price)}")
        return
    users_mod.set_target(user, prod["id"], price)
    users_mod.watch(user, prod["id"])  # a target you don't hear about is useless
    _, best_st = catalog.best_state(prod, prices)
    now_p = best_st.get("price") if best_st else None
    name = alerts.esc(prod["name"])
    if now_p is not None and now_p <= price:
        send_message(chat_id, tr(user, "target_already", name=name,
                                 price=alerts.fmt_eur(price),
                                 now_p=alerts.fmt_eur(now_p)))
    else:
        away = (f" ({(now_p - price) / price * 100:.0f}% away)"
                if now_p is not None else "")
        send_message(chat_id, tr(user, "target_set", name=name,
                                 price=alerts.fmt_eur(price), away=away))


def _cmd_tag(chat_id, products: dict, user: dict, ud: dict, arg: str, on: bool) -> None:
    """/tag <name-part> #tag — organize the shared catalog (Q24). Owner or adder."""
    parts = arg.split()
    if len(parts) != 2 or not parts[1].startswith("#"):
        send_message(chat_id, f"Usage: /{'tag' if on else 'untag'} &lt;name-part&gt; #tag")
        return
    prod = _find_one(chat_id, products, parts[0])
    if prod is None:
        return
    if not _can_touch(user, prod):
        send_message(chat_id, "⛔ Only the owner or whoever added this product can tag it.")
        return
    changed = catalog.set_tag(prod, parts[1]) if on else catalog.unset_tag(prod, parts[1])
    if changed:
        tags_txt = " ".join(f"#{t}" for t in prod.get("tags") or [])
        verb = "Tagged" if on else "Removed tag"
        send_message(chat_id, f"🏷 {verb} <b>{alerts.esc(prod['name'])}</b>"
                              + (f" — now: {alerts.esc(tags_txt)}" if tags_txt else "."))
    else:
        send_message(chat_id, "Nothing to change.")


def _cmd_search(chat_id, products: dict, bot_state: dict, user: dict, arg: str,
                add_stager) -> None:
    """/search <name> — one-off aggregator price check; [Track] to keep one (Q27)."""
    if not arg:
        send_message(chat_id, "Usage: /search &lt;product-name&gt;")
        return
    query = arg.strip()
    if any(p["id"] == catalog.slugify(query) for p in products["products"]):
        send_message(chat_id, f"Already tracking <b>{alerts.esc(query)}</b> — "
                              "/where for its stores.")
        return
    if add_stager is None:
        send_message(chat_id, "Search runs on the tracker — try /add (guided) for now.")
        return
    add_stager(chat_id, query, user, bot_state, search_mode=True)


def _cmd_watch(chat_id, products: dict, user: dict, arg: str) -> None:
    if not arg:
        ids = users_mod.effective_ids(user, [p["id"] for p in products["products"]])
        if not ids:
            send_message(chat_id, "Empty watchlist — /watch &lt;name-part&gt; or tap ⭐ "
                                  "on any alert card.")
            return
        names = []
        for pid in ids:
            prod = next((p for p in products["products"] if p["id"] == pid), None)
            names.append(f"• {alerts.esc((prod or {}).get('name') or pid)}")
        head = ("⭐ <b>You hear about everything</b> (owner watch-all).\n"
                if user.get("watch_all") else "⭐ <b>Your watchlist</b>\n")
        send_message(chat_id, head + "\n".join(names))
        return
    prod = _find_one(chat_id, products, arg)
    if prod is None:
        return
    if users_mod.watch(user, prod["id"]):
        send_message(chat_id, tr(user, "watching", name=alerts.esc(prod["name"])))
    else:
        send_message(chat_id, tr(user, "already_watching", name=alerts.esc(prod["name"])))


def _cmd_unwatch(chat_id, products: dict, user: dict, arg: str) -> None:
    if not arg:
        send_message(chat_id, "Usage: /unwatch &lt;name-part&gt;")
        return
    prod = _find_one(chat_id, products, arg)
    if prod is None:
        return
    if users_mod.unwatch(user, prod["id"]):
        send_message(chat_id, tr(user, "unwatched", name=alerts.esc(prod["name"])))
    else:
        send_message(chat_id, tr(user, "not_watching", name=alerts.esc(prod["name"])))


def _cmd_mute(chat_id, products: dict, user: dict, arg: str, on: bool) -> None:
    if not arg:
        send_message(chat_id, f"Usage: /{'mute' if on else 'unmute'} &lt;name-part&gt;")
        return
    prod = _find_one(chat_id, products, arg)
    if prod is None:
        return
    changed = users_mod.mute(user, prod["id"]) if on else users_mod.unmute(user, prod["id"])
    if not changed:
        send_message(chat_id, tr(user, "nothing_to_change"))
    elif on:
        send_message(chat_id, tr(user, "muted", name=alerts.esc(prod["name"])))
    else:
        send_message(chat_id, tr(user, "unmuted", name=alerts.esc(prod["name"])))


def _cmd_remove(chat_id, products: dict, user: dict, ud: dict, arg: str,
                confirmed: bool) -> None:
    if not arg:
        send_message(chat_id, "Usage: /remove &lt;name-part&gt;")
        return
    matches = catalog.find_products(products, arg)
    if not matches:
        send_message(chat_id, f"Nothing matches “{alerts.esc(arg)}”.")
        return
    if len(matches) > 1:
        listing = "\n".join(f"• {alerts.esc(p['name'] or p['id'])}" for p in matches)
        send_message(chat_id, f"Multiple matches — be more specific:\n{listing}")
        return
    prod = matches[0]
    if not _can_touch(user, prod):
        send_message(chat_id, "⛔ Only the owner or whoever added this product can remove it.")
        return
    if not confirmed:
        send_card(chat_id, f"🗑 Remove <b>{alerts.esc(prod['name'])}</b> and all its "
                           f"price history? This cannot be undone.",
                  kb_confirm_remove(prod["id"]))
        return
    products["products"] = [p for p in products["products"] if p is not prod]
    for u in ud.get("users") or []:
        users_mod.unwatch(u, prod["id"])
        users_mod.unmute(u, prod["id"])
    send_message(chat_id, tr(user, "removed", name=alerts.esc(prod["name"]),
                             stores=f"{len(prod['offers'])} store(s)"))


def _send_settings(chat_id, user: dict) -> None:
    prefs = user.get("prefs") or {}
    send_card(chat_id,
              f"⚙️ <b>Settings</b> — {alerts.esc(users_mod.fmt_user(user))}\n"
              "Tap a row to change.\n"
              f"• Digest: {'on' if prefs.get('digest') else 'off'} at "
              f"{prefs.get('digest_hour_utc')} UTC\n"
              f"• Quiet hours: {prefs.get('quiet') or 'off'}\n"
              f"• Language: {prefs.get('lang') or 'en'}",
              kb_settings(user))


# ---------------------------------------------------------------------------
# Callback handling (poll mode; the Worker mirrors this in webhook mode)
# ---------------------------------------------------------------------------

def handle_callback(cb: dict, products: dict, prices: dict, bot_state: dict,
                    ud: dict) -> int | None:
    """One callback_query update. Returns the chat id if handled."""
    data = cb.get("data") or ""
    chat_id = (cb.get("message") or {}).get("chat", {}).get("id")
    if not chat_id or not data:
        return None
    answer_callback(cb.get("id"), "")
    user = users_mod.get(ud, chat_id)

    if data == "noop":
        return chat_id

    if data == "req":
        if user is not None and user.get("status") == users_mod.ST_ACTIVE:
            send_message(chat_id, "You're already in — /help for commands.")
            return chat_id
        return _handle_stranger(ud, chat_id, {"from": cb.get("from") or {}}, user)

    if user is None or user.get("status") != users_mod.ST_ACTIVE:
        return None  # only active members press the rest

    action, _, rest = data.partition(":")

    if action in ("wt", "mu"):
        prod = _prod_by_id(products, rest)
        if prod is None:
            return chat_id
        if action == "wt":
            if prod["id"] in (user.get("watchlist") or []):
                users_mod.unwatch(user, prod["id"])
                send_message(chat_id, tr(user, "unwatched",
                                         name=alerts.esc(prod["name"])))
            else:
                users_mod.watch(user, prod["id"])
                send_message(chat_id, tr(user, "watching",
                                         name=alerts.esc(prod["name"])))
        else:
            if prod["id"] in (user.get("muted") or []):
                users_mod.unmute(user, prod["id"])
                send_message(chat_id, tr(user, "unmuted",
                                         name=alerts.esc(prod["name"])))
            else:
                users_mod.mute(user, prod["id"])
                send_message(chat_id, tr(user, "muted",
                                         name=alerts.esc(prod["name"])))
        return chat_id

    if action == "wh":
        prod = _prod_by_id(products, rest)
        if prod is not None:
            _cmd_where(chat_id, {"products": [prod]}, prices, prod["id"])
        return chat_id

    if action == "ch":
        prod = _prod_by_id(products, rest)
        if prod is not None:
            _cmd_chart(chat_id, {"products": [prod]}, prices, prod["id"])
        return chat_id

    if action == "ok":
        _cmd_remove(chat_id, products, user, ud, rest, confirmed=True)
        return chat_id

    if action == "th":  # legacy confirm cards (phase 1) — kept until old chats clear
        slug, _, pct_raw = rest.partition(":")
        _cmd_threshold(chat_id, products, bot_state, user, ud, f"{slug} {pct_raw}",
                       confirmed=True)
        return chat_id

    if action == "cf":
        # short-key confirmation (phase 2): run the stored command as confirmed
        ops = bot_state.get("pending_ops") or {}
        op = ops.get(rest)
        if op is None:
            send_message(chat_id, "That confirmation expired — run the command again.")
            return chat_id
        if op.get("chat_id") not in (None, chat_id) and not users_mod.is_owner(ud, chat_id):
            return chat_id  # only the requester (or owner) may confirm
        ops.pop(rest, None)
        confirmed_update = {"update_id": 0, "confirmed": True,
                            "message": {"chat": {"id": chat_id},
                                        "text": op.get("cmd") or ""}}
        handle_update(confirmed_update, products, prices, bot_state, ud)
        return chat_id

    if action == "chk":
        send_message(chat_id, "🔎 This run already re-scrapes everything — results "
                              "arrive with the run report.")
        return chat_id

    if action == "apr":
        if not users_mod.is_owner(ud, chat_id):
            return chat_id
        u = users_mod.approve(ud, int(rest))
        if u is not None:
            send_message(chat_id, f"✅ Approved <b>{alerts.esc(users_mod.fmt_user(u))}</b>.")
            send_message(int(rest), "🎉 You're in!\n\n" + HELP)
        return chat_id

    if action == "dec":
        if not users_mod.is_owner(ud, chat_id):
            return chat_id
        u = users_mod.decline(ud, int(rest))
        if u is not None:
            send_message(chat_id, f"❌ Declined <b>{alerts.esc(users_mod.fmt_user(u))}</b>.")
            send_message(int(rest), "The owner declined your access request — sorry!")
        return chat_id

    if action == "add":
        idx_s, _, key = rest.partition(":")
        _cmd_pick(chat_id, products, bot_state, user, ud, f"{key} {idx_s}", confirmed=True)
        return chat_id

    if action == "st":
        _settings_callback(chat_id, user, rest)
        return chat_id

    return chat_id


def _settings_callback(chat_id, user: dict, rest: str) -> None:
    key = rest.split(":")[0]
    prefs = user.setdefault("prefs", {})
    if key == "digest":
        users_mod.set_pref(user, "digest", not prefs.get("digest"))
    elif key == "hour":
        cur = prefs.get("digest_hour_utc") or 21
        order = list(users_mod.DIGEST_HOURS_UTC)
        if cur in order:
            users_mod.set_pref(user, "digest_hour_utc",
                               order[(order.index(cur) + 1) % len(order)])
    elif key == "quiet":
        cur = prefs.get("quiet") or "off"
        keys = list(users_mod.QUIETS)
        if cur in keys:
            users_mod.set_pref(user, "quiet", keys[(keys.index(cur) + 1) % len(keys)])
    elif key == "lang":
        cur = prefs.get("lang") or "en"
        users_mod.set_pref(user, "lang", "lv" if cur == "en" else "en")
    _send_settings(chat_id, user)


def _prod_by_id(products: dict, slug: str) -> dict | None:
    return next((p for p in products.get("products") or [] if p["id"] == slug), None)
