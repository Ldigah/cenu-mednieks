"""Pure command logic — parsing, entry creation, matching. No network here (testable)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import stores


def parse_command(text: str):
    """'/add@MyBot https://… nice stand' → ('add', 'https://… nice stand'); else None."""
    if not text or not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].split("@")[0].lower()
    if not cmd:
        return None
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:60] or "item"


def make_entry(raw_url: str, name: str = ""):
    """Build a tracking entry from a URL (+ optional display name).

    Returns (entry, None) or (None, error_message).
    """
    url = (raw_url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = (urlparse(url).hostname or "").removeprefix("www.")
    if not host or "." not in host:
        return None, f"That doesn't look like a product URL: {raw_url}"

    adapter = stores.adapter_for(url)
    is_auto = adapter.domain not in stores.STORES  # unknown shop → auto JSON-LD adapter
    entry = {
        "id": f"{slugify(name or 'item')}-{slugify(host)}",
        "name": (name or "").strip(),
        "store_label": host,
        "url": url,
        "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    note = ""
    if is_auto:
        note = ("\n⚠️ Unknown shop — I'll try JSON-LD auto-detection; "
                "if it fails 3× I'll tell you and a store preset is needed.")
    return entry, note


def find_entries(products: dict, part: str) -> list[dict]:
    part = (part or "").lower().strip()
    if not part:
        return []
    return [e for e in products["products"]
            if part in e["id"].lower() or part in e["name"].lower() or part in e["url"].lower()]


def entry_label(e: dict) -> str:
    return f"{e['name'] or e['id']} · {e.get('store_label', '')}"
