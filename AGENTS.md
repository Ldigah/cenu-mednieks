# Cenu Mednieks — Agent Instructions

## Project

Price & discount tracker: GitHub Actions runs `scraper.py` 3×/day (07/15/23 Latvia =
`0 5,13,21 * * *` UTC, plus `repository_dispatch` from the Worker), scrapes the offers
of every **product** in the state gist — free stores every run, the ScraperAPI-backed
aggregators once a day on the first run — diffs against last-seen prices, and alerts on
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
  `state.py`, `fetch.py` have unit tests; keep network code thin around them. `fetch.py`'s
  tests fake the HTTP session, and stand in for Amazon's wall with a local HTTP server +
  real Chromium (skipped where no browser is installed).
- Lint + types gate: `ruff check .` and `mypy .` must pass (CI enforces; config in
  `pyproject.toml`).

## Useful checks

- `pytest -q` — unit tests (no network; requires `pip install -r requirements-dev.txt`)
- `ruff check . && mypy .` — lint + types
- `python scraper.py --local --only <name-part>` — local dry run (state-local/, no Telegram)
- `python scraper.py --health` — failures-only report, one line per failing offer with its
  `last_error` (same as the weekly workflow; under the daily cadence it reports the 05:00
  run's aggregator results unless that run was delayed). Against the live gist it would
  also post the report to Telegram — run it as
  `TELEGRAM_BOT_TOKEN= python scraper.py --health` to keep that quiet.
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
- Amazon.pl in CI: Playwright failed on ~half the fetches (35-run history to 2026-11-23),
  always as a bare `wait_for_selector` timeout. The reproduced cause is a bot wall whose
  button lands on the storefront root (seen from a home IP; on a runner it is inferred
  from the identical signature): `fetch_playwright` clicks through, then re-navigates
  to the deep link. `AMAZON_PL.api_fallback=True` rescues a still-blocked attempt with one
  plain ScraperAPI fetch (it returned the real page on every measurement; 5 credits). A
  wait timeout now reports `[landed on <url> — '<title>']` — read that before touching
  selectors.
- `getUpdates` must be called with `offset = last_update_id + 1` or commands
  re-process forever.
- ScraperAPI does not charge a flat credit per call, and this project only scrapes the
  expensive kinds of page: **10 credits per page behind bot protection** (salidzini,
  ceno — Cloudflare), 5 per Amazon page, 1 for an ordinary site, +10 rendered
  (official rate table, confirmed against the account counter 2026-11-23). Only 2xx
  responses are billed, so a challenge page returned as HTTP 200 is a billed 10.
  `fetch_scraperapi` reads each response's `sa-credit-cost` header and `usage` counts
  real credits. At 3 runs/day six aggregator offers cost ≈180 credits/day ≈ 5,400/month,
  more than the account's 5,000 — hence `daily_only` on both aggregators (decided
  2026-11-23): six offers × 10 = 60 credits once a day. Amazon and hinnavaatlus are on
  the free ladder and still run 3×/day.
- salidzini.lv: `api_country="lv"` plain fetch on the day's first run (`daily_only`;
  geotargeting is free). An
  LV exit gets past the Turnstile on ~3 of 4 calls (measured 2026-11-23); the rest
  return the challenge page as HTTP 200, which `fetch_scraperapi` retries on a fresh
  exit — the failed page is billed either way, so the retry costs nothing per
  successful page. Without the geo pin even renders fail. ceno.lv: the same, no pin.
  kurpirkt.lv is UNREACHABLE via ScraperAPI (500 on plain/render/premium, 403
  ultra_premium — verified 2026-11-19): no kurpirkt offers in CI; its preset remains
  for local runs from home IPs.
- Aggregator query hygiene (learned on `lenovo thinkpad t16 gen4`, which never produced a
  price): a `/search?q=` page is the ~40 cheapest matches, and slug URLs plus queries that
  resolve to a product-group page (`lenovo thinkpad t16 …` → `/lenovo-thinkpad-t16-gen-4`)
  are hCaptcha-walled from every IP — drop the brand word. `gen4` as one word matched
  nothing on ceno.lv; listings say `Gen 4`. For a product with cheaper accessories (a
  laptop) add a token only the real item has (`… Gen 4 16GB`), or the min is a 22 € screen
  protector. There is deliberately no generic accessory filter: an accessory can be the
  product itself (the LP923 is a phone holder).
- Runs are routinely partial — the daily cadence skips the aggregators, and offers fail —
  so `product.last_min` (the previous run's best) is no sound reference for the "new
  best" alert: the cheapest source returning unchanged after a run that only saw dearer
  stores read as a drop (JBL: 73 € after a run recorded at 77 € = −5.2%), and a small real
  drop cleared the threshold against that dearer stand-in. The alert compares like with
  like: `catalog.previous_best(results, last_seen)`, the lowest last-seen price among the
  offers scraped in this run. `last_min` is only the fallback when nothing has a history.
- ScraperAPI budget: ≈60 credits for the day's aggregator scrape at 3 products, plus
  ~10/day for salidzini retries and ~15/day for Amazon rescues — ≈90/day ≈ 2,700/month
  against the account's 5,000. The account API reported `requestLimit: 5000` on
  2026-11-23, not the 1,000 `SCRAPERAPI_MONTHLY_CREDITS` assumes, so the 80% warning
  fires early rather than late.
- `publish.py` (not `site.py`) — `site` is Python's stdlib and shadows any local
  module of that name.
- The Worker's read-only answers are approximations of the Python bot's replies; charts
  and mutations always go through the Actions run.
- State writes go through `merge_write` — don't reintroduce plain `store.write` for
  products/prices/bot_state in `scraper.py`, or overlapping runs will clobber each other.
