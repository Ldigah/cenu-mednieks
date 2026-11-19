"""Users, roles, watchlists, quotas and per-user preferences (users.json).

Multi-user model (DECISIONS.md, 2026-11-19 — the 40 questions):
  - Approved users only: strangers get a friendly blurb + [Request access];
    the owner approves with one tap (Q1/Q5/Q35).
  - Roles: owner + member (Q6). The owner administers, members watch/add within
    a soft quota of 10 products (Q7).
  - One shared catalog; a per-user watchlist decides what pings whom (Q2/Q3).
    The owner starts with watch_all=true (everything, like v1 alerts).
  - Alerts are personal DMs, silent until a member watches something (Q4/Q17).
  - users.json lives in the secret gist next to products/prices (Q36).

Pure logic only — no network. merge_users() keeps concurrent runner writes safe.
"""
from __future__ import annotations

from datetime import UTC, datetime

from commands import slugify

USERS_VERSION = 1

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"

ST_ACTIVE = "active"
ST_PENDING = "pending"
ST_BLOCKED = "blocked"

QUOTA_DEFAULT = 10           # Q7: soft cap on products a member can add (owner exempt)
DIGEST_HOURS_UTC = (5, 13, 21)  # CI runs 3×/day — only these digest hours exist
QUIETS = {                   # Q19: per-user quiet windows (UTC hours), run-time selects
    "off": None,
    "23-8": (23, 8),         # 23:00–08:00 — covers the 21:00 UTC digest-time run alerts
}


def new_user(chat_id, name: str = "", username: str = "", role: str = ROLE_MEMBER,
             status: str = ST_ACTIVE) -> dict:
    return {"chat_id": int(chat_id), "name": name or "", "username": username or "",
            "role": role, "status": status,
            "watch_all": role == ROLE_OWNER,   # owner sees everything by default
            "watchlist": [], "muted": [], "quota": QUOTA_DEFAULT,
            "added": datetime.now(UTC).isoformat(timespec="seconds"),
            "prefs": {"digest": role == ROLE_OWNER, "digest_hour_utc": DIGEST_HOURS_UTC[2],
                      "quiet": "off", "lang": "en"}}


def ensure(users: dict | None) -> dict:
    """Normalize a loaded users.json (or empty) to the current shape."""
    out: dict = {"version": USERS_VERSION, "users": []}
    for u in (users or {}).get("users") or []:
        base = new_user(u.get("chat_id") or 0, u.get("name") or "",
                        u.get("username") or "", u.get("role") or ROLE_MEMBER,
                        u.get("status") or ST_ACTIVE)
        base["watch_all"] = bool(u.get("watch_all", base["watch_all"]))
        base["watchlist"] = [str(p) for p in u.get("watchlist") or []]
        base["muted"] = [str(p) for p in u.get("muted") or []]
        base["quota"] = int(u["quota"]) if isinstance(u.get("quota"), (int, float)) \
            else QUOTA_DEFAULT
        if u.get("added"):
            base["added"] = u["added"]
        prefs = dict(base["prefs"])
        prefs.update({k: v for k, v in (u.get("prefs") or {}).items() if v is not None})
        base["prefs"] = prefs
        out["users"].append(base)
    return out


def get(users: dict, chat_id) -> dict | None:
    cid = int(chat_id) if chat_id is not None else None
    for u in users.get("users") or []:
        if u.get("chat_id") == cid:
            return u
    return None


def owner(users: dict) -> dict | None:
    for u in users.get("users") or []:
        if u.get("role") == ROLE_OWNER:
            return u
    return None


def active(users: dict) -> list[dict]:
    return [u for u in users.get("users") or [] if u.get("status") == ST_ACTIVE]


def pending(users: dict) -> list[dict]:
    return [u for u in users.get("users") or [] if u.get("status") == ST_PENDING]


def is_owner(users: dict, chat_id) -> bool:
    u = get(users, chat_id)
    return bool(u and u.get("role") == ROLE_OWNER and u.get("status") == ST_ACTIVE)


def is_member(users: dict, chat_id) -> bool:
    u = get(users, chat_id)
    return bool(u and u.get("status") == ST_ACTIVE)


# ---------------------------------------------------------------------------
# Onboarding (Q5: owner approves; Q35: friendly request for strangers)
# ---------------------------------------------------------------------------

def migrate_owner(users: dict, chat_id, name: str = "", username: str = "") -> dict:
    """First boot: the configured chat (TELEGRAM_CHAT_ID / bot_state.chat_id) becomes owner."""
    if owner(users) is not None:
        return users
    if chat_id:
        users.setdefault("users", []).append(
            new_user(chat_id, name or "Owner", username, ROLE_OWNER, ST_ACTIVE))
    return users


def request_access(users: dict, chat_id, name: str = "", username: str = "") -> bool:
    """Stranger tapped [Request access] (or /start). True if a NEW request was created."""
    u = get(users, chat_id)
    if u is not None:
        if u.get("status") == ST_PENDING:
            return False
        return False  # active/blocked users already have an answer
    users.setdefault("users", []).append(
        new_user(chat_id, name, username, ROLE_MEMBER, ST_PENDING))
    return True


def approve(users: dict, chat_id) -> dict | None:
    u = get(users, chat_id)
    if u is None or u.get("status") != ST_PENDING:
        return None
    u["status"] = ST_ACTIVE
    return u


def decline(users: dict, chat_id) -> dict | None:
    u = get(users, chat_id)
    if u is None or u.get("status") != ST_PENDING:
        return None
    u["status"] = ST_BLOCKED
    return u


def block(users: dict, chat_id) -> dict | None:
    """Owner removes a member's access (kept on file so they can't re-request silently)."""
    u = get(users, chat_id)
    if u is None or u.get("role") == ROLE_OWNER:
        return None
    u["status"] = ST_BLOCKED
    return u


# ---------------------------------------------------------------------------
# Watchlist / mute (Q3, Q15)
# ---------------------------------------------------------------------------

def wants_alert(user: dict, product_id: str) -> bool:
    if not user or user.get("status") != ST_ACTIVE:
        return False
    pid = str(product_id)
    if pid in (user.get("muted") or []):
        return False
    return bool(user.get("watch_all")) or pid in (user.get("watchlist") or [])


def watch(user: dict, product_id: str) -> bool:
    pid = str(product_id)
    wl = user.setdefault("watchlist", [])
    if pid in wl:
        return False
    wl.append(pid)
    user["muted"] = [m for m in user.get("muted") or [] if m != pid]
    return True


def unwatch(user: dict, product_id: str) -> bool:
    pid = str(product_id)
    wl = user.get("watchlist") or []
    if pid not in wl:
        return False
    user["watchlist"] = [p for p in wl if p != pid]
    return True


def mute(user: dict, product_id: str) -> bool:
    pid = str(product_id)
    m = user.setdefault("muted", [])
    if pid in m:
        return False
    m.append(pid)
    return True


def unmute(user: dict, product_id: str) -> bool:
    pid = str(product_id)
    m = user.get("muted") or []
    if pid not in m:
        return False
    user["muted"] = [p for p in m if p != pid]
    return True


def effective_ids(user: dict, all_ids: list[str]) -> list[str]:
    """Product ids this user actually hears about (watch_all → everything)."""
    if user.get("watch_all"):
        return [i for i in all_ids if i not in (user.get("muted") or [])]
    wl = user.get("watchlist") or []
    return [i for i in wl if i not in (user.get("muted") or [])]


# ---------------------------------------------------------------------------
# Quota (Q7)
# ---------------------------------------------------------------------------

def products_added_by(products: dict, chat_id) -> int:
    cid = int(chat_id) if chat_id is not None else -1
    return sum(1 for p in products.get("products") or []
               if int(p.get("added_by") or -1) == cid)


def quota_left(user: dict, products: dict) -> int | None:
    """None = unlimited (owner). A quota of 0 is a legit hard stop."""
    if user.get("role") == ROLE_OWNER:
        return None
    q = user.get("quota")
    if not isinstance(q, (int, float)):
        q = QUOTA_DEFAULT
    return max(0, int(q) - products_added_by(products, user.get("chat_id")))


# ---------------------------------------------------------------------------
# Preferences (Q19/Q32)
# ---------------------------------------------------------------------------

def set_pref(user: dict, key: str, value) -> bool:
    """Validated pref write. Returns False for unknown keys/values."""
    prefs = user.setdefault("prefs", {})
    if key == "digest":
        prefs["digest"] = bool(value)
        return True
    if key == "digest_hour_utc":
        h = int(value)
        if h not in DIGEST_HOURS_UTC:
            return False
        prefs["digest_hour_utc"] = h
        return True
    if key == "quiet":
        if value not in QUIETS:
            return False
        prefs["quiet"] = value
        return True
    if key == "lang":
        if value not in ("en", "lv"):
            return False
        prefs["lang"] = value
        return True
    return False


def is_quiet_now(user: dict, now: datetime) -> bool:
    """Q19 quiet window check. Windows wrap midnight (e.g. 23→8)."""
    win = QUIETS.get((user.get("prefs") or {}).get("quiet") or "off")
    if not win:
        return False
    start, end = win
    h = now.hour
    if start < end:
        return start <= h < end
    return h >= start or h < end


# ---------------------------------------------------------------------------
# Merge (concurrent runners + Worker gist writes)
# ---------------------------------------------------------------------------

def merge_users(theirs: dict | None, ours: dict) -> dict:
    """Union by chat_id; per-record, ours wins field-wise (last writer wins per user).

    Deletions don't happen in phase 1 (blocked keeps the record), so union-merge
    is safe for overlapping runs.
    """
    out = ensure(theirs)
    for u in ours.get("users") or []:
        cid = u.get("chat_id")
        found = None
        for i, existing in enumerate(out["users"]):
            if existing.get("chat_id") == cid:
                found = i
                break
        if found is None:
            out["users"].append(ensure({"users": [u]})["users"][0])
        else:
            merged = dict(out["users"][found])
            merged.update({k: v for k, v in u.items() if k != "chat_id"})
            out["users"][found] = merged
    return out


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_user(u: dict) -> str:
    label = u.get("name") or u.get("username") or str(u.get("chat_id"))
    if u.get("username"):
        label = f"{label} (@{u['username']})"
    return label


def product_id_for(name: str) -> str:
    return slugify(name)
