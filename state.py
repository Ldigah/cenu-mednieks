"""State storage: GitHub Gist backend (CI, nothing committed to the repo) or local dir (dev)."""
from __future__ import annotations

import json
from pathlib import Path

import requests

from config import F_BOT, F_PRICES, F_PRODUCTS, GIST_ID, GIST_TOKEN, SEED_PRODUCTS_FILE, STATE_DIR

GIST_API = "https://api.github.com/gists"


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

    def load(self) -> "GistStore":
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
        r = requests.patch(f"{GIST_API}/{self.gist_id}", headers=self._headers(),
                           json={"files": {filename: {"content": content}}}, timeout=20)
        r.raise_for_status()
        self._files[filename] = {"content": content, "truncated": False}


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
