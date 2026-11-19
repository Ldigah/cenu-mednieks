# Cenu Mednieks — Agent Instructions

## Project

Price & discount tracker: GitHub Actions runs `scraper.py` 3×/day (07/15/23 Latvia =
`0 5,13,21 * * *` UTC, plus `repository_dispatch` from the Worker), scrapes every offer
of every **product** in the state gist, diffs against last-seen prices, and alerts on
Telegram. A Cloudflare Worker gives instant read-only commands; a static snapshot
publishes to GitHub Pages. Decisions: [DECISIONS.md](DECISIONS.md) ·
Setup: [SETUP.md](SETUP.md).

## Architecture in one paragraph

`products.json` / `prices.json` / `bot_state.json` / `lock.json` live in a secret GitHub
Gist (`state.py`). The catalog is **product-centric** (`catalog.py`, schema v2): each
product has `offers[]` (store URLs discovered via `/add <name>` or attached with
`/add-url`); `prices.json` stays keyed by offer URL. Each run: poll Telegram
`getUpdates` and process commands (`telegram_io.py`) → scrape each offer
(`fetch.py`: ScraperAPI for Cloudflare-guarded aggregators → requests → Playwright
fallback; `stores/`: per-domain adapters, JSON-LD first, CSS fallback) → product-level
min drives drop alerts (per-product threshold) and all-time-low alerts; discounts and
restocks fire per offer (`alerts.py`) → daily digest on the 21:00 UTC run → write state
back to the gist **merged** over anything another runner wrote (`merge_write`). The
Cloudflare Worker (`worker/`) answers read-only commands from a 60 s KV cache and
dispatches runs for mutating ones; `publish.py` renders the GitHub Pages / Mini App
snapshot. Nothing is committed to the repo.

## Conventions

- Python 3.11 in CI. Deps pinned in `requirements.txt` (requests, bs4, dotenv,
  matplotlib, playwright); dev deps in `requirements-dev.txt` (pytest, ruff, mypy).
- All code, comments, commit messages, and docs in English. Replies always in English.
- Never commit secrets. Local runs use gitignored `.env`; CI uses GitHub Secrets
  (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GIST_TOKEN`, `GIST_ID`, `SCRAPERAPI_KEY`).
  The Worker adds `TELEGRAM_BOT_TOKEN`/`GIST_TOKEN`/`GH_TOKEN`/`WEBHOOK_SECRET` as
  wrangler secrets.
- Adding a store = new adapter in `stores/` + `register()` in `stores/__init__.py`.
  Adding a product = `/add <name>` or `/add <url>` in Telegram (no code changes).
- Telegram messages use `parse_mode: HTML` and must `html.escape` any page-derived text.
- Test-first for pure logic: `parse.py`, `alerts.py`, `catalog.py`, `commands.py`,
  `state.py` have unit tests; keep network code thin around them.
- Lint + types gate: `ruff check .` and `mypy .` must pass (CI enforces; config in
  `pyproject.toml`).

## Useful checks

- `pytest -q` — unit tests (no network; requires `pip install -r requirements-dev.txt`)
- `ruff check . && mypy .` — lint + types
- `python scraper.py --local --only <name-part>` — local dry run (state-local/, no Telegram)
- `python scraper.py --health` — failures-only report (same as the weekly workflow)
- `python publish.py --out site --local` — render the web snapshot locally
- Logs in CI: Actions → "Price tracker" → "Run tracker" step
  (`OK <product> · <store>: <price>` / `FAIL <product> · <store>: <reason>`)
- Live state: the secret gist (URL like `https://gist.github.com/<user>/<id>`)

## Subagents (in `.zcode/agents/`)

- `selector-finder` — given a product URL, proposes extraction strategy + entry (use
  for new stores/products).
- `scraper-doctor` — diagnoses failing entries (selector drift, anti-bot, layout change).

## Gotchas

- Cron is UTC; GitHub has no DST → summer runs drift 1 h earlier local. Accepted.
- GitHub disables schedules after 60 days without repo activity — any push resets it.
- Amazon may 503 datacenter IPs; the `requests+playwright` strategy handles it — never
  remove the fallback.
- `getUpdates` must be called with `offset = last_update_id + 1` or commands
  re-process forever.
- salidzini.lv: `api_country="lv"` plain fetches every run (an LV exit avoids the
  Turnstile — verified 2026-11-19; without the geo pin even renders fail). ceno.lv: plain
  every run. Budget ≈180/1,000 credits per month. kurpirkt.lv is UNREACHABLE via
  ScraperAPI (500 on plain/render/premium, 403 ultra_premium — verified 2026-11-19):
  no kurpirkt offers in CI; its preset remains for local runs from home IPs.
- `publish.py` (not `site.py`) — `site` is Python's stdlib and shadows any local
  module of that name.
- The Worker's read-only answers are approximations of the Python bot's replies; charts
  and mutations always go through the Actions run.
- State writes go through `merge_write` — don't reintroduce plain `store.write` for
  products/prices/bot_state in `scraper.py`, or overlapping runs will clobber each other.
