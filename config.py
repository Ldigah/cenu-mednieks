"""Central configuration: environment variables + tuned constants.

Constant values reflect the answers to QUESTIONS.md (2026-11-18, see DECISIONS.md).
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv optional in CI (env comes from workflow)
    pass

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
GIST_ID = os.environ.get("GIST_ID", "")

# --- tuned constants (QUESTIONS.md answers) ---
DROP_ALERT_MIN_PCT = 1.0      # Q8: alert on drops >= 1%
FAIL_ALERT_AFTER = 3          # Q21: alert after 3 consecutive failures
DIGEST_RUN_HOUR_UTC = 21      # Q11: daily digest rides the 21:00 UTC run (= 23:00 Latvia, winter)
HISTORY_MAX_POINTS = 500      # per-entry price history cap (~5 months at 3 runs/day)

REQUEST_TIMEOUT = 20
FETCH_RETRIES = 2

STATE_DIR = "state-local"     # local backend directory (gitignored)
SEED_PRODUCTS_FILE = "seed_products.json"

# Files kept inside the state gist
F_PRODUCTS = "products.json"
F_PRICES = "prices.json"
F_BOT = "bot_state.json"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "lv-LV,lv;q=0.9,en-US;q=0.8,en;q=0.7,de;q=0.6,pl;q=0.5",
}
