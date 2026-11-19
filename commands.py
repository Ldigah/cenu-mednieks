"""Pure command parsing — /cmd extraction and slugify. No network here (testable).

Product/offer model logic lives in catalog.py.
"""
from __future__ import annotations

import re


def parse_command(text: str):
    """'/add@MyBot https://… nice stand' → ('add', 'https://… nice stand'); else None.

    Hyphens normalize to underscores so BotFather-registered names (a-z, 0-9, _
    only) match: /add-url and /add_url both arrive as 'add_url'.
    """
    if not text or not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].split("@")[0].lower().replace("-", "_")
    if not cmd:
        return None
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:60] or "item"


def parse_price_arg(text: str) -> float | None:
    """/target-style price argument: '65', '65,99', '65.99 €' → float, else None."""
    raw = (text or "").strip().rstrip("€").strip().replace(" ", "").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value == value else None  # NaN guard
