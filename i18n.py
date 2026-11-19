"""User-facing strings, en + lv (Q16: English first, i18n-ready; Latvian covers the
high-visibility surfaces — alerts, digest, intro. Long reference texts like /help
stay English for now; completing the switch end-to-end is phase 3).

Usage: from i18n import T; T("lv", "digest_header")
T() always returns a usable string (falls back to en, then the key itself).
Placeholders use str.format-style {fields} where needed.
"""
from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "intro_title": "👋 Hi! I'm <b>Cenu Mednieks</b> — I hunt prices &amp; discounts "
                       "across Latvian, Baltic and EU shops.",
        "intro_body": "Access is by approval: tap the button and the owner will let you in. "
                      "Once approved you can track products, build a watchlist and get "
                      "deal alerts.",
        "digest_header": "🛒 <b>Daily price summary</b> — {date}",
        "digest_empty": "Nothing to summarize yet — /watch a product or /add one.",
        "overnight": "🌙 <b>Overnight alerts</b>:",
        "drop_alert": "📉 <b>{name}</b> — new best <b>{price}</b> at {store}",
        "atl_alert": "🏆 <b>{name}</b> all-time low: <b>{price}</b> at {store}",
        "target_alert": "🎯 <b>{name}</b> hit your target: <b>{price}</b> "
                        "(target {target}) at {store}",
        "discount_alert": "🏷 <b>{label}</b> discount: <b>{price}</b> (reg. {old}, −{pct}%)",
        "restock_alert": "♻️ <b>{label}</b> back in stock at <b>{price}</b>",
        "failure_alert": "⚠️ <b>{label}</b> failed {fails}× in a row — {reason}.",
        "watching": "⭐ Watching <b>{name}</b> — drop/ATL alerts will ping you here.",
        "unwatched": "💔 Unwatched <b>{name}</b> — no more alerts for it.",
        "muted": "🔕 Muted <b>{name}</b> — still watched, just silent.",
        "unmuted": "🔔 Unmuted <b>{name}</b>.",
        "budget_line": "💳 ScraperAPI: {credits}/{total} credits this month",
    },
    "lv": {
        "intro_title": "👋 Sveiki! Esmu <b>Cenu Mednieks</b> — sekoju cenām un atlaidēm "
                       "Latvijas, Baltijas un ES veikalos.",
        "intro_body": "Piekļuve ar apstiprinājumu: nospied pogu un īpašnieks tevi ielaus. "
                      "Kad apstiprināts, vari sekot produktiem, veidot sarakstu un "
                      "saņemt atlaižu paziņojumus.",
        "digest_header": "🛒 <b>Ikdienas cenu kopsavilkums</b> — {date}",
        "digest_empty": "Vēl nav ko kopsavilkot — /watch produktu vai /add.",
        "overnight": "🌙 <b>Nakts paziņojumi</b>:",
        "drop_alert": "📉 <b>{name}</b> — jauna labākā cena <b>{price}</b> veikalā {store}",
        "atl_alert": "🏆 <b>{name}</b> visu laiku zemākā cena: <b>{price}</b> veikalā {store}",
        "target_alert": "🎯 <b>{name}</b> sasniedza tavu mērķi: <b>{price}</b> "
                        "(mērķis {target}) veikalā {store}",
        "discount_alert": "🏷 <b>{label}</b> atlaide: <b>{price}</b> (bij. {old}, −{pct}%)",
        "restock_alert": "♻️ <b>{label}</b> atkal noliktavā par <b>{price}</b>",
        "failure_alert": "⚠️ <b>{label}</b> neizdevās {fails}× pēc kārtas — {reason}.",
        "watching": "⭐ Sekoju <b>{name}</b> — cenu pazeminājumi tevi sasniegs šeit.",
        "unwatched": "💔 Vairs nesekoju <b>{name}</b>.",
        "muted": "🔕 <b>{name}</b> apklusināts — vēl sekoju, tikai bez paziņojumiem.",
        "unmuted": "🔔 <b>{name}</b> atkal ar paziņojumiem.",
        "budget_line": "💳 ScraperAPI: {credits}/{total} kredīti šajā mēnesī",
    },
}


def T(lang: str | None, key: str, **fields) -> str:
    """Translate key for lang (falls back: lang → en → key), formatting {fields}."""
    text = STRINGS.get(lang or "en", {}).get(key) \
        or STRINGS["en"].get(key) or key
    if fields:
        try:
            return text.format(**fields)
        except (KeyError, IndexError, ValueError):
            return text
    return text
