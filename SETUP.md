# SETUP — from zero to running bot (~15 minutes, 100% free)

Fully autonomous: everything runs on GitHub Actions (+ an optional Cloudflare
Worker for instant commands). **No home PC needed.**

## Step 1 — Create the Telegram bot (do this NOW)

1. In Telegram, open **@BotFather** → send `/newbot` → follow prompts (name it e.g. `Cenu Mednieks`).
2. BotFather replies with an **API token** like `123456789:ABCdef…`. Copy it → this is `TELEGRAM_BOT_TOKEN`.
3. Open a chat with your new bot and send `/start` (any message works — it "unlocks" the chat).

**Chat ID (optional but recommended):** open
`https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find `"chat":{"id":123456789,…}`.
→ this is `TELEGRAM_CHAT_ID`.
*If you skip this, the bot will auto-adopt your chat the first time you send it `/start` after deployment.*

## Step 2 — Create the two GitHub PATs

**State gist PAT** (scopes: `gist` only):

1. GitHub → avatar → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**.
2. Note: `cenu-mednieks state` · Expiration: no expiration (or 1 year) · Scopes: **`gist`** only.
3. Generate → copy → this is `GIST_TOKEN`.

**Dispatch PAT** (only needed for the Cloudflare Worker in Step 7 — classic `repo` scope,
or a fine-grained PAT with *Contents: read & write* on this repo). → this is `GH_TOKEN`.

## Step 3 — Add the secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token from Step 1.2 |
| `TELEGRAM_CHAT_ID` | chat id from Step 1 (skip if you'll use auto-adopt) |
| `GIST_TOKEN` | PAT from Step 2 |
| `GIST_ID` | *leave for now — filled in Step 4* |
| `SCRAPERAPI_KEY` | from Step 5 (optional but recommended — add later if you like) |

## Step 4 — Create the state gist

**Option A (no local Python needed — recommended):** just run the workflow.
Repo → **Actions** tab → enable workflows if asked → select **Price tracker** → **Run workflow** → **Run**.
The first run creates the gist automatically and prints `GIST_ID=…` in the "Run tracker" step log
(and messages it to Telegram if `TELEGRAM_CHAT_ID` is set).

**Option B (local Python):** install Python 3.11+, then:

```bash
pip install -r requirements.txt
copy .env.example .env        # fill in GIST_TOKEN (and the Telegram values)
python bootstrap.py           # prints GIST_ID=…
```

Copy the `GIST_ID` and add it as the last missing secret, then run the workflow once more —
your Telegram gets *"✅ Cenu Mednieks initialized"*.

## Step 5 — ScraperAPI key (free, unlocks the LV aggregators in CI)

salidzini.lv / kurpirkt.lv / ceno.lv Cloudflare-challenge datacenter IPs, so CI fetches
them through [ScraperAPI](https://www.scraperapi.com)'s free plan (1,000 credits/month,
no credit card — plain fetch = 1 credit, JS-rendered = 10):

1. Sign up → copy the API key → add it as the `SCRAPERAPI_KEY` secret.
2. That's it. The budget (verified 2026-11-19): salidzini + ceno plain 3×/day with LV
   geo-pinning (`country_code=lv` avoids the Turnstile — 1 credit each) →
   **≈180 of 1,000 credits/month**. kurpirkt.lv is unreachable via ScraperAPI (HTTP 500
   on every proxy tier — verified) and is not tracked in CI; its preset stays for local
   runs from home.
3. Without the key the bot still works — those three stores simply fall back to the
   direct requests→Playwright ladder (fine from residential IPs, e.g. local dry runs).

## Step 6 — GitHub Pages (the public price page / Mini App)

The workflow publishes a static snapshot (`site.py` → [publish.py](publish.py)) after every
scheduled run. One-time enable:

1. Repo → **Settings** → **Pages** → **Source: GitHub Actions**.
2. Run the workflow once → your page goes live at `https://<your-username>.github.io/cenu-mednieks/`.

**Make it a Telegram Mini App (optional):** point the bot's menu button at that URL —

```bash
curl "https://api.telegram.org/bot<TOKEN>/setChatMenuButton" \
  -H "content-type: application/json" \
  -d '{"menu_button":{"type":"web_app","text":"📊 Prices","web_app":{"url":"https://<your-username>.github.io/cenu-mednieks/"}}}'
```

Opening the bot now shows a **📊 Prices** button that opens the price list inside Telegram.

## Step 7 — Instant commands: the Cloudflare Worker (optional, free)

Commands are processed by CI 3×/day. For ~1-second answers to `/prices /where /stats /best /list`
and ~2-minute `/add`, deploy the Worker — full instructions in [worker/README.md](worker/README.md)
(≈5 minutes: `wrangler deploy` + one `setWebhook` call). Inline queries (`@yourbot jbl` in any
chat) come from the same Worker.

## Verify it works

- Actions → **Price tracker**: latest run green, "Run tracker" log shows `OK <product> · <store>: <price>` lines.
- Telegram: `/prices` shows best price per product; `/add jbl clip 5` tracks a new product everywhere at once.
- After a few runs: `/chart jbl` (min-line + per-store lines + ATL marker), `/stats jbl`, `/best`.
- Pages: `https://<your-username>.github.io/cenu-mednieks/` shows the public price page.

## Telegram command reference

| Command | Effect |
|---|---|
| `/add <name>` | track a product on salidzini.lv + kurpirkt.lv + ceno.lv (discovery) |
| `/add <url> [name]` | track one specific store page |
| `/add-url <url> [name-part]` | attach another store (Amazon, direct shop) to a product |
| `/threshold <part> <pct>` | per-product drop-alert threshold (default 5%) |
| `/list` `/prices` | catalog / best price per product |
| `/where <part>` | all stores + prices for one product |
| `/stats [part]` `/best` | price statistics / deepest current discounts |
| `/chart [part]` | price-history PNG (best-price line + per-store + ATL) |
| `/remove <part>` | stop tracking a product |
| `/check` | forces a re-scrape on the current run |

Alerts: best-price drops ≥ threshold, new all-time lows, new/deeper discounts, restocks.
Price increases are recorded silently. Failures alert at 3 consecutive fails, then every 5th.

## Upgrading from the v1 layout (pre-2026-11-19)

The old flat `products.json` (one URL per entry) **migrates automatically** on the first
run of the new code: entries are grouped by product name, one offer per old URL, threshold
defaults to 5%. Price history in `prices.json` (keyed by URL) is kept as-is. The old
`setup-local-task.ps1` home-PC task is gone — delete the scheduled task on the PC
(`Unregister-ScheduledTask -TaskName 'Cenu Mednieks local tracker'`) and remove the local
`.env`'s telegram entries if you want the PC fully out of the loop.

## Troubleshooting

| Symptom | Fix |
|---|---|
| First run exits with `GIST_ID=…` banner | expected once — add the secret and re-run |
| `FAIL … scraperapi HTTP 429` | monthly credit pool dry — check scraperapi.com usage page; salidzini/ceno still run (cheap), kurpirkt waits for next day |
| `FAIL amazon.de … HTTP 503` | Amazon blocked the run — Playwright fallback should kick in; if it persists, run the `scraper-doctor` agent |
| `price not found on page` | selectors drifted — run the `scraper-doctor` agent on that entry |
| Schedule didn't fire at exact time | GitHub schedules can lag 3–15 min; any push resets the 60-day disable timer |
| Commands not answered | they're processed on the next run — trigger **Run workflow**, or deploy the Worker (Step 7) |
| Pages 404 | Settings → Pages → Source must be **GitHub Actions**, and a scheduled run must have completed |
