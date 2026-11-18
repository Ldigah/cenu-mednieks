# Cenu Mednieks — Agent Instructions

## Project

Price & discount tracker: GitHub Actions runs `scraper.py` 3×/day (07/15/23 Latvia = `0 5,13,21 * * *` UTC), scrapes the products in the **state gist** (not the repo), diffs against last-seen prices, and alerts on Telegram. Decisions: [DECISIONS.md](DECISIONS.md) · Setup: [SETUP.md](SETUP.md).

## Architecture in one paragraph

`products.json` / `prices.json` / `bot_state.json` live in a secret GitHub Gist (`state.py`). Each run: poll Telegram `getUpdates` and process commands (`telegram_io.py`) → scrape every entry (`fetch.py`: requests → Playwright fallback; `stores.py`: per-domain adapters, JSON-LD first, CSS fallback) → diff & alert (`alerts.py`: drops ≥1%, new/deeper discounts, restocks; increases silent) → daily digest on the 21:00 UTC run → write state back to the gist. Nothing is committed to the repo.

## Conventions

- Python 3.11 in CI. Deps pinned in `requirements.txt` (requests, bs4, dotenv, matplotlib, playwright); dev deps in `requirements-dev.txt`.
- All code, comments, commit messages, and docs in English. Replies to the user are always in English (global rule).
- Never commit secrets. Local runs use gitignored `.env`; CI uses GitHub Secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GIST_TOKEN`, `GIST_ID`).
- Adding a store = new `StoreAdapter` in `stores.py`. Adding a product = `/add <url>` in Telegram (no code changes).
- Telegram messages use `parse_mode: HTML` and must `html.escape` any page-derived text.
- Test-first for pure logic: `parse.py`, `alerts.py`, `commands.py`, `state.py` have unit tests; keep network code thin around them.

## Useful checks

- `pytest -q` — unit tests (no network; requires `pip install -r requirements-dev.txt`)
- `python scraper.py --local --only <name-part>` — local dry run (state-local/, no Telegram)
- `python scraper.py --health` — failures-only report (same as the weekly workflow)
- Logs in CI: Actions → "Price tracker" → "Run tracker" step (`OK <id>: <price>` / `FAIL <id>: <reason>`)
- Live state: the secret gist (URL like `https://gist.github.com/<user>/<id>`)

## Subagents (in `.zcode/agents/`)

- `selector-finder` — given a product URL, proposes extraction strategy + entry (use for new stores/products).
- `scraper-doctor` — diagnoses failing entries (selector drift, anti-bot, layout change).

## Gotchas

- Cron is UTC; GitHub has no DST → summer runs drift 1 h earlier local. Accepted (Q13).
- GitHub disables schedules after 60 days without repo activity — any push resets it.
- Amazon may 503 datacenter IPs; the `requests+playwright` strategy handles it — never remove the fallback.
- `getUpdates` must be called with `offset = last_update_id + 1` or commands re-process forever.
