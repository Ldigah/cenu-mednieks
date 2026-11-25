"""State storage: GitHub Gist backend (CI, nothing committed to the repo) or local dir (dev).

Two runners may overlap on the same gist (scheduled + manual dispatch), so every write
after scraping goes through merge_write: re-read fresh, merge ours over it, write.
Merges are pure functions (merge_prices / merge_products / merge_bot_state) so the
last-writer-wins semantics are unit-testable. A best-effort lock file (lock.json)
reduces duplicated scraping while merges make overlap harmless.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from config import (
    F_LOCK,
    GIST_ID,
    GIST_TOKEN,
    GIST_WRITE_BACKOFF,
    GIST_WRITE_RETRIES,
    SEED_PRODUCTS_FILE,
    STATE_DIR,
)

GIST_API = "https://api.github.com/gists"

# Transient write failures worth retrying: 409 is the gist backend still committing
# the previous revision (see config.GIST_WRITE_RETRIES), 5xx is upstream flakiness.
GIST_WRITE_RETRY_STATUS = {409, 502, 503, 504}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class GistStore:
    """products.json / prices.json / bot_state.json live in one secret gist."""

    def __init__(self, gist_id: str, token: str):
        if not gist_id or not token:
            raise ValueError("GIST_ID and GIST_TOKEN must be set (see SETUP.md)")
        self.gist_id = gist_id
        self.token = token
        self._files: dict = {}

    def _headers(self):
        return {"Authorization": f"token {self.token}",
                "Accept": "application/vnd.github+json"}

    def load(self) -> GistStore:
        r = requests.get(f"{GIST_API}/{self.gist_id}", headers=self._headers(), timeout=20)
        r.raise_for_status()
        self._files = r.json().get("files", {})
        return self

    def read(self, filename, default=None):
        f = self._files.get(filename)
        if not f:
            return default
        try:
            if f.get("truncated"):
                text = requests.get(f["raw_url"], timeout=20).text
            else:
                text = f.get("content") or ""
            return json.loads(text)
        except (json.JSONDecodeError, requests.RequestException, TypeError):
            return default

    def write(self, filename, data):
        content = json.dumps(data, indent=2, ensure_ascii=False)
        for attempt in range(GIST_WRITE_RETRIES + 1):
            r = requests.patch(f"{GIST_API}/{self.gist_id}", headers=self._headers(),
                               json={"files": {filename: {"content": content}}}, timeout=20)
            if r.status_code not in GIST_WRITE_RETRY_STATUS or attempt == GIST_WRITE_RETRIES:
                break
            time.sleep(GIST_WRITE_BACKOFF * 2 ** attempt)
        r.raise_for_status()
        self._files[filename] = {"content": content, "truncated": False}

    def merge_write(self, filename: str, data: dict, merge_fn) -> None:
        """Read the file fresh, merge ours over it, then write.

        This closes the read-modify-write gap between the load at run start and the
        write at run end (another runner may have scraped in between).
        """
        try:
            fresh = self.read(filename)
        except requests.RequestException:
            fresh = None
        merged = merge_fn(fresh, data) if isinstance(fresh, dict) and isinstance(data, dict) \
            else data
        self.write(filename, merged)

    def acquire_lock(self, runner_id: str, ttl_minutes: int = 10) -> bool:
        """Best-effort advisory lock (the gist API has no compare-and-swap)."""
        now = _now_iso()
        if not lock_is_free(self.read(F_LOCK), runner_id, now):
            return False
        self.write(F_LOCK, {"runner": runner_id,
                            "expires": (datetime.now(UTC)
                                        + timedelta(minutes=ttl_minutes))
                            .isoformat(timespec="seconds")})
        return True

    def release_lock(self, runner_id: str) -> None:
        try:
            lock = self.read(F_LOCK) or {}
            if lock.get("runner") == runner_id:
                self.write(F_LOCK, {})
        except requests.RequestException:
            pass  # a stale lock expires via its TTL — nothing to crash a run over


class LocalStore:
    """File-based backend for local dry runs (state-local/, gitignored)."""

    def __init__(self, root=STATE_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self):
        return self

    def read(self, filename, default=None):
        p = self.root / filename
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

    def write(self, filename, data):
        (self.root / filename).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def create_gist(token: str, files: dict, description="cenu-mednieks state") -> str:
    payload = {
        "description": description,
        "public": False,
        "files": {name: {"content": json.dumps(data, indent=2, ensure_ascii=False)}
                  for name, data in files.items()},
    }
    r = requests.post(GIST_API, headers={"Authorization": f"token {token}",
                                         "Accept": "application/vnd.github+json"},
                      json=payload, timeout=20)
    r.raise_for_status()
    return r.json()["id"]


def seed_products() -> dict:
    p = Path(SEED_PRODUCTS_FILE)
    if not p.exists():
        return {"products": []}
    return json.loads(p.read_text(encoding="utf-8"))


def default_store(local: bool):
    if local:
        return LocalStore().load()
    return GistStore(GIST_ID, GIST_TOKEN).load()


# ---------------------------------------------------------------------------
# Merges (pure — unit-tested in tests/test_state.py)
# ---------------------------------------------------------------------------

def lock_is_free(lock: dict | None, runner_id: str, now_iso_ts: str) -> bool:
    """Free when unheld, held by us, or expired (missing expires counts as expired)."""
    lock = lock or {}
    holder = lock.get("runner")
    if not holder or holder == runner_id:
        return True
    return (lock.get("expires") or "") <= now_iso_ts


def _merge_hist(a: list | None, b: list | None) -> list:
    """Union of history points by timestamp; later argument wins on duplicates."""
    pts: dict = {}
    for pt in list(a or []) + list(b or []):
        if isinstance(pt, dict) and pt.get("t"):
            pts[pt["t"]] = pt.get("p")
    return [{"t": t, "p": pts[t]} for t in sorted(pts)]


def merge_prices(theirs: dict | None, ours: dict | None) -> dict:
    """Per-URL: the fresher record's scalars win; histories from both are unioned."""
    theirs = theirs or {}
    ours = ours or {}
    out: dict = {}
    for url in set(theirs) | set(ours):
        t = theirs.get(url) or {}
        o = ours.get(url) or {}
        if not t:
            out[url] = o
        elif not o:
            out[url] = t
        elif (o.get("last_checked") or "") >= (t.get("last_checked") or ""):
            out[url] = dict(o, history=_merge_hist(o.get("history"), t.get("history")))
        else:
            out[url] = dict(t, history=_merge_hist(o.get("history"), t.get("history")))
    return out


def merge_products(theirs: dict | None, ours: dict) -> dict:
    """Union of catalog products (v2, keyed by id); products updated by this run win.

    v1 strays (flat entries without an offers key — e.g. an old-code runner wrote
    the gist between our load and write) are ignored, not merged as products.
    """
    merged: dict = {}
    for p in (theirs or {}).get("products", []):
        if isinstance(p, dict) and "offers" not in p:
            continue
        merged[p.get("id")] = p
    for p in ours.get("products", []):
        merged[p.get("id")] = p
    return {"version": ours.get("version") or 2, "products": list(merged.values())}


def merge_bot_state(theirs: dict | None, ours: dict) -> dict:
    """Offset takes the max (never re-process); chat adoption from ours.

    New in multi-user phase: pending_alerts / pending_adds union per chat,
    usage takes the higher credit count within the same month (Q38).
    """
    out = dict(theirs or {})
    out["update_offset"] = max(int((theirs or {}).get("update_offset") or 0),
                               int(ours.get("update_offset") or 0))
    if ours.get("chat_id"):
        out["chat_id"] = ours["chat_id"]
    for key in ("pending_alerts", "pending_adds", "pending_ops"):
        merged = dict((theirs or {}).get(key) or {})
        for chat, val in (ours.get(key) or {}).items():
            if isinstance(val, list) and isinstance(merged.get(chat), list):
                merged[chat] = merged[chat] + [x for x in val
                                               if x not in merged[chat]]
            else:
                merged[chat] = val
        if merged:
            out[key] = merged
    t_usage = (theirs or {}).get("usage") or {}
    o_usage = ours.get("usage") or {}
    if o_usage or t_usage:
        if (o_usage.get("month") or "") >= (t_usage.get("month") or ""):
            base = dict(t_usage)
            base.update({k: v for k, v in o_usage.items() if k != "credits"})
            base["credits"] = max(int(o_usage.get("credits") or 0),
                                  int(t_usage.get("credits") or 0)
                                  if t_usage.get("month") == o_usage.get("month") else 0)
            out["usage"] = base
        else:
            out["usage"] = t_usage
    return out
