# Cenu Mednieks — Price & Discount Tracking Bot

> **Status: IMPLEMENTED (v1, 2026-11-18).** The 30 questions were answered — see [DECISIONS.md](DECISIONS.md).
> The architecture below was amended during implementation; where it differs from the code, the code wins.
> Key changes vs this original plan: state + product list live in a **secret GitHub Gist** (Q15: no repo commits — so no `permissions: contents: write` either), products are managed via **Telegram commands** (`/add`, `/list`, `/prices`, `/chart`, `/remove`), the "dashboard" is `/chart` PNGs inside Telegram, and products are **JSON, not YAML**.

## What this bot does

- Checks a configured list of products (Latvian stores + Amazon.de) **3× per day**, fully free, on GitHub Actions.
- Extracts **current price, old (crossed-out) price, discount %, and availability**.
- Compares against stored state and **alerts on Telegram only when something happens**: price drop, new/bigger discount, target price reached, restock, or repeated scrape failure (selector drift).
- Persists price history as JSON committed back to the repo.

## Architecture overview

```text
products.yaml  (you edit: what to track)
      │
      ▼   cron 3×/day (GitHub Actions, runs in UTC)
scraper.py ──► per-store adapters (220.lv, 1a.lv, ksenukai.lv, …, amazon.de)
      │           JSON-LD structured data first, CSS selectors as fallback
      ▼
state/prices.json  (last seen prices/discounts, committed back to the repo)
      │
      ├── price dropped / discount appeared / target hit ──► Telegram alert
      ├── nothing changed ─────────────────────────────────► silence (or daily digest)
      └── state updated & committed back ──► also keeps the schedule enabled
```

Key handling decisions:

- **Config, not code** — products live in `products.yaml`; adding a product never touches Python.
- **Store presets** — selectors per store are defined once; per-product selectors only as an override.
- **JSON-LD first** — most shops embed `application/ld+json` with the price; far more stable than CSS classes.
- **Change-only alerts** — 3 silent runs/day beat 3 spammy reports; you get pinged when it matters.
- **State in repo** — survives runner teardown; auto-commits also prevent GitHub from disabling the schedule after 60 days of repo inactivity.

---

## Phase 1 — Telegram bot & credentials

1. **Create the bot**
   - Open Telegram, talk to **`@BotFather`**, send `/newbot`, follow the prompts.
   - Save the **API Token** (looks like `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`) securely.

2. **Get your Chat ID**
   - Send your new bot a dummy message ("Hello").
   - Open `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` in a browser.
   - Find `"chat":{"id":XXXXXXXXX,...}` in the JSON — that number is your **Chat ID** (negative for groups).

## Phase 2 — Repository structure

New **private** GitHub repository (e.g. `cenu-mednieks`):

```text
cenu-mednieks/
├── .github/
│   └── workflows/
│       └── scheduler.yml      # cron 3×/day + manual trigger + state commit
├── .zcode/
│   └── agents/                # ZCode subagents for maintenance
│       ├── selector-finder.md
│       └── scraper-doctor.md
├── scraper.py                 # entry point: fetch → parse → diff → alert → save state
├── stores.py                  # per-store extraction presets (JSON-LD / CSS)
├── products.yaml              # YOUR tracked products — the only file you edit
├── state/
│   └── prices.json            # last-seen prices; auto-committed by CI
├── .env.example               # template for local runs (real .env is gitignored)
├── requirements.txt
├── AGENTS.md                  # instructions for AI coding agents on this repo
├── PLAN.md                    # this file
└── QUESTIONS.md               # 30 open questions gating implementation
```

Example `products.yaml`:

```yaml
products:
  - name: "Sony WH-1000XM5"
    url: "https://www.220.lv/lv/…"
    target_price: 250        # optional: alert when price ≤ this
  - name: "Some gadget (Amazon.de)"
    url: "https://www.amazon.de/-/en/dp/B0EXAMPLE"
    target_price: 99
```

## Phase 3 — Dependencies (`requirements.txt`)

```text
requests==2.31.0
beautifulsoup4==4.12.3
PyYAML==6.0.1
python-dotenv==1.0.1
```

(Playwright gets added later, only for stores that prove unreachable with plain requests.)

## Phase 4 — The scraper (`scraper.py`)

Blueprint-level sketch — the important parts are real, glue is elided:

```python
import json, os, re
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = Path("state/prices.json")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept-Language": "lv-LV,lv;q=0.9,en-US;q=0.8,en;q=0.7",
}

def parse_price(text: str) -> float | None:
    """'1 299,99 €' / '12.99' → float, else None."""
    m = re.search(r"(\d[\d\s.,]*)", text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "")
    if "," in raw and "." in raw:          # 1.299,99 (thousands + comma decimal)
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")        # 1299,99
    try:
        return float(raw)
    except ValueError:
        return None

def extract(entry, soup):
    """→ dict(price, old_price, in_stock). JSON-LD first, stores.py CSS preset fallback."""
    ...  # <script type="application/ld+json"> offers.price / availability
    ...  # fallback: preset["price"], preset["old_price"] via soup.select_one()

def diff(entry, new, old) -> list[str]:
    """Alert-worthy events for one product."""
    events = []
    if old and new["price"] is not None and old.get("price") is not None:
        if new["price"] < old["price"]:
            events.append(f"📉 {old['price']:.2f} → {new['price']:.2f} €")
        # price rises: recorded in state, not alerted (configurable)
    if new.get("old_price") and not (old or {}).get("old_price"):
        events.append(f"🏷 discount appeared (was {new['old_price']:.2f} €)")
    if entry.get("target_price") and new["price"] and new["price"] <= entry["target_price"]:
        events.append(f"🎯 target hit (≤ {entry['target_price']:.2f} €)")
    return events

def send_telegram(lines):
    """HTML parse mode — Markdown breaks on product names containing _ or *."""
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "parse_mode": "HTML",
              "text": "\n".join(lines)},
        timeout=15,
    )

def main():
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    products = yaml.safe_load(Path("products.yaml").read_text(encoding="utf-8"))["products"]

    for entry in products:                      # fetch → extract → diff
        ...                                     # retries with backoff, failure counter

    now = datetime.now(timezone.utc).isoformat()
    for url, rec in state.items():
        rec["last_checked"] = now
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__":
    main()
```

State record shape (`state/prices.json`):

```json
{
  "https://www.220.lv/lv/…": {
    "price": 249.99,
    "old_price": 299.99,
    "in_stock": true,
    "consecutive_failures": 0,
    "first_seen": "2026-11-18T05:00:12+00:00",
    "last_checked": "2026-11-18T13:00:08+00:00"
  }
}
```

## Phase 5 — GitHub Secrets

Never hardcode the token or chat ID:

1. Repo → **Settings** → **Secrets and variables** → **Actions**.
2. Add `TELEGRAM_BOT_TOKEN` (BotFather token) and `TELEGRAM_CHAT_ID` (your chat ID).

## Phase 6 — Workflow (`.github/workflows/scheduler.yml`)

```yaml
name: Price Tracker Bot

on:
  schedule:
    # 05:00, 13:00, 21:00 UTC = 07:00, 15:00, 23:00 Latvia time in winter (EET = UTC+2).
    # GitHub cron has no DST: in summer these land an hour earlier local time.
    - cron: '0 5,13,21 * * *'
  workflow_dispatch: {}   # allows manual trigger from the Actions tab

permissions:
  contents: write          # needed to commit state/prices.json back

concurrency:
  group: price-tracker
  cancel-in-progress: false

jobs:
  run-tracker:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run price scraper
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python scraper.py

      - name: Commit updated state
        run: |
          git config user.name "price-bot"
          git config user.email "actions@github.com"
          git add state/prices.json
          git diff --cached --quiet || git commit -m "chore: update price state [skip ci]"
          git push
```

Notes:
- **Cron is UTC.** The original draft's `0 7,15,23 * * *` would run at 09:00/17:00/01:00 Latvia time — corrected above.
- Scheduled runs can start 3–15 min late at busy times; harmless for price tracking.
- Private repos get 2,000 free Actions minutes/month; 3 short runs/day uses roughly 100–200 min.
- The state commit doubles as repo activity, preventing GitHub's 60-day schedule auto-disable.

## Phase 7 — The subdomain (`bot.yourdomain.com`)

GitHub Actions does all the work for free, so the WordPress subdomain is optional. Later uses:

- **Dashboard**: scraper already writes `state/prices.json` into the repo; serve it raw (`raw.githubusercontent.com`) and render a small static HTML chart page on the subdomain, or publish via GitHub Pages.
- No webhook infrastructure needed until you want live features.

## Anti-bot strategy ladder (Amazon.de especially)

1. `requests` + realistic headers (above) — often enough for Latvian shops, frequently **not** enough for Amazon from datacenter IPs (503/captcha).
2. **Playwright** headless fallback for blocked domains — slower (~3–5 min runs), bigger install.
3. Scraping-API free tiers (ScraperAPI, Zenrows…) if 1–2 fail.
4. Amazon PA-API (needs affiliate sales history) or Keepa API as clean alternatives.

**Latvian shortcut:** [salidzini.lv](https://www.salidzini.lv) aggregates prices from nearly all Latvian e-shops. Tracking a product there means one stable page layout, one selector set, and the minimum price across all stores at once — often better than scraping each shop separately.

## Corrections made to the original draft

| Issue in draft | Fix |
|---|---|
| `from bs4 માટે import BeautifulSoup` (stray Gujarati word) | `from bs4 import BeautifulSoup` |
| `workflow_dispatch: Allows manual trigger…` — invalid YAML, comment parsed as a value | `workflow_dispatch: {}` with a proper `#` comment |
| Cron `0 7,15,23 * * *` labelled as local-time runs | Corrected to `0 5,13,21 * * *` UTC (= 07/15/23 EET) |
| `parse_mode: "Markdown"` breaks on names with `_`/`*` | Switched to `parse_mode: "HTML"` |
| No state, no discount capture, alerts every run | Added `state/prices.json` diffing, discount/target/restock events, change-only alerts, state commit + `permissions: contents: write` |

## Roadmap

1. Answer [QUESTIONS.md](QUESTIONS.md) (all 30).
2. `git init`, push to a new private GitHub repo, add Secrets.
3. Implement `stores.py` presets for the chosen stores (JSON-LD first).
4. Wire Telegram, run 1–2 days with "every run" reporting to validate parsing.
5. Switch to change-only alerts; add Amazon fallback tier if needed.
6. Optional: Telegram commands (`/prices`), dashboard on the subdomain.
