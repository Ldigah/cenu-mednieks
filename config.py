"""Central configuration: environment variables + tuned constants.

Constant values reflect the answers to QUESTIONS.md (2026-11-18, see DECISIONS.md).
"""
import os
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv optional in CI (env comes from workflow)
    pass

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
# ScraperAPI — lets CI scrape the Cloudflare-guarded LV aggregators, which block
# GitHub's IPs. Not a flat credit per call: ScraperAPI charges 10 per page behind
# bot protection (salidzini, ceno), 5 per Amazon page, 1 for ordinary sites, +10
# rendered (measured 2026-11-23; each response states its own figure in the
# sa-credit-cost header). Without the key, those stores fall back to the direct
# requests/playwright ladder (works from residential IPs, e.g. local runs).
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")


def _normalize_gist_id(raw: str) -> str:
    """Accept the bare id or a pasted gist URL (https://gist.github.com/user/<id>)."""
    raw = (raw or "").strip()
    m = re.search(r"([0-9a-fA-F]{32})", raw)
    return m.group(1) if m else raw.rstrip("/").rsplit("/", 1)[-1]


GIST_ID = _normalize_gist_id(os.environ.get("GIST_ID", ""))

# --- tuned constants (QUESTIONS.md answers + 2026-11-19 rework) ---
DROP_ALERT_MIN_PCT = 1.0      # legacy offer-level floor (discount/restock events)
DEFAULT_DROP_PCT = 5.0        # product-level min-price drop threshold (per-product override via /threshold)
FAIL_ALERT_AFTER = 3          # Q21: alert after 3 consecutive failures
DIGEST_RUN_HOUR_UTC = 21      # Q11: daily digest rides the 21:00 UTC run (= 23:00 Latvia, winter)
HISTORY_MAX_POINTS = 500      # per-entry price history cap (~5 months at 3 runs/day; longer for daily offers)

REQUEST_TIMEOUT = 20
FETCH_RETRIES = 1              # one retry max — long anti-bot backoffs waste the run budget

# Gist writes occasionally 409 right after a previous write: GitHub's storage is still
# committing the last revision (PyGithub #1815; the 2026-11-25 15:14 UTC run lost
# prices.json this way, ~2 s after the products.json write). A short backoff ride it out.
GIST_WRITE_RETRIES = 3
GIST_WRITE_BACKOFF = 1.5       # seconds before the first retry, doubled each attempt

STATE_DIR = "state-local"     # local backend directory (gitignored)
SEED_PRODUCTS_FILE = "seed_products.json"

# Files kept inside the state gist
F_PRODUCTS = "products.json"
F_PRICES = "prices.json"
F_BOT = "bot_state.json"
F_LOCK = "lock.json"          # runner lock: {"runner": id, "expires": iso} — see state.py
F_USERS = "users.json"        # Q36: users/roles/watchlists live in the same secret gist

# ScraperAPI budget (Q38): track credits per run in bot_state.usage, warn at 80%.
# 1000 is the budget the project was planned around; the account's real limit may
# differ (its API reported requestLimit 5000 on 2026-11-23). Aggregator pages are
# fetched once a day (daily_only) at 10 credits each — about 60 for the first run
# of the day at three products.
SCRAPERAPI_MONTHLY_CREDITS = 1000
USAGE_WARN_FRACTION = 0.8

# Guided /add (Q30): keep at most this many candidates in a pick-from-matches card
ADD_CANDIDATES_MAX = 3

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "lv-LV,lv;q=0.9,en-US;q=0.8,en;q=0.7,de;q=0.6,pl;q=0.5",
}
