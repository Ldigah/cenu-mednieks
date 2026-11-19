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
# ScraperAPI (free plan: 1000 credits/month) — lets CI scrape the Cloudflare-guarded
# LV aggregators (salidzini/ceno 1 credit plain, kurpirkt 10 credits rendered).
# Without it, those stores fall back to the direct requests/playwright ladder
# (works from residential IPs, e.g. local runs).
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
HISTORY_MAX_POINTS = 500      # per-entry price history cap (~5 months at 3 runs/day)

REQUEST_TIMEOUT = 20
FETCH_RETRIES = 1              # one retry max — long anti-bot backoffs waste the run budget

STATE_DIR = "state-local"     # local backend directory (gitignored)
SEED_PRODUCTS_FILE = "seed_products.json"

# Files kept inside the state gist
F_PRODUCTS = "products.json"
F_PRICES = "prices.json"
F_BOT = "bot_state.json"
F_LOCK = "lock.json"          # runner lock: {"runner": id, "expires": iso} — see state.py

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "lv-LV,lv;q=0.9,en-US;q=0.8,en;q=0.7,de;q=0.6,pl;q=0.5",
}
