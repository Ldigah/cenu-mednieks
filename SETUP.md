# SETUP — from zero to running bot (~15 minutes)

Do the steps in order. **Create the Telegram bot first (Step 1) — everything else depends on its token.**

## Step 1 — Create the Telegram bot (do this NOW)

1. In Telegram, open **@BotFather** → send `/newbot` → follow prompts (name it e.g. `Cenu Mednieks`).
2. BotFather replies with an **API token** like `123456789:ABCdef…`. Copy it → this is `TELEGRAM_BOT_TOKEN`.
3. Open a chat with your new bot and send `/start` (any message works — it "unlocks" the chat).

**Chat ID (optional but recommended):** open
`https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find `"chat":{"id":123456789,…}`.
→ this is `TELEGRAM_CHAT_ID`.
*If you skip this, the bot will auto-adopt your chat the first time you send it `/start` after deployment.*

## Step 2 — Create a GitHub PAT for the state gist

Nothing is committed to the repo; the bot keeps `products.json` / `prices.json` in a **secret Gist**.

1. GitHub → click your avatar → **Settings** → **Developer settings** (bottom-left) → **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**.
2. Note: `cenu-mednieks state` · Expiration: `No expiration` (or 1 year, then renew) · Scopes: tick **only `gist`**.
3. Generate → copy the token → this is `GIST_TOKEN`.

## Step 3 — Create the repo and push this code

```bash
cd C:/Users/Leon/Coding/cenu-mednieks
git init -b main
git add .
git commit -m "Cenu Mednieks v1: tracker, Telegram bot, workflows"
# create a PRIVATE repo named cenu-mednieks on github.com first, then:
git remote add origin https://github.com/<your-username>/cenu-mednieks.git
git push -u origin main
```

*(Or upload via GitHub's web UI: "New repository" → private → push/upload these files.)*

## Step 4 — Add the secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**, four times:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token from Step 1.2 |
| `TELEGRAM_CHAT_ID` | chat id from Step 1 (skip if you'll use auto-adopt) |
| `GIST_TOKEN` | PAT from Step 2 |
| `GIST_ID` | *leave for now — filled in Step 6* |

## Step 5 — Create the state gist

**Option A (no local Python needed — recommended):** just run the workflow.
Repo → **Actions** tab → enable workflows if asked → select **Price tracker** → **Run workflow** → **Run**.
The first run creates the gist automatically and prints `GIST_ID=…` in the "Run tracker" step log
(and messages it to Telegram if `TELEGRAM_CHAT_ID` is set).

**Option B (local Python):** install Python 3.11+ (`winget install Python.Python.3.11`), then:

```bash
pip install -r requirements.txt
copy .env.example .env        # fill in GIST_TOKEN (and the Telegram values)
python bootstrap.py           # prints GIST_ID=…
```

## Step 6 — Finish

1. Copy the `GIST_ID` from Step 5 and add it as the fourth secret (repo → Settings → Secrets → Actions).
2. **Actions → Price tracker → Run workflow** once more. Your Telegram gets
   *"✅ Cenu Mednieks initialized — tracking N entries"*.
3. In Telegram, send `/list` then **Run workflow** again — you should get the list back.
   From now on commands sent between runs are answered on the next run (3×/day), or instantly after a manual **Run workflow**.

## Step 7 — Local PC runs (hybrid mode)

GitHub's datacenter IPs are blocked by **salidzini.lv** (Cloudflare Turnstile) and **pigu.lt** (Cloudflare challenge) — but your home IP passes. So the work is split:

| Runner | Stores | Schedule |
|---|---|---|
| GitHub Actions (free) | amazon.de, amazon.pl, hinnavaatlus.ee, any future `/add` | 07:00/15:00/23:00 Latvia (cron, UTC) |
| Your PC (Task Scheduler) | salidzini.lv, pigu.lt | 07:15/15:15/23:15 Latvia |

Both write to the **same state gist** and both answer Telegram commands — the split is invisible to you.

One-time setup on the PC:

```powershell
# 1. Python 3.11+ (skip if installed)
winget install -e --id Python.Python.3.11

# 2. Dependencies (in the repo folder)
cd C:\Users\Leon\Coding\cenu-mednieks
python -m pip install -r requirements.txt

# 3. Fill .env — paste your TELEGRAM_BOT_TOKEN and GIST_TOKEN (GIST_ID is prefilled)
notepad .env

# 4. Register the 3×/day scheduled task
powershell -ExecutionPolicy Bypass -File .\setup-local-task.ps1

# 5. Test one local run right now
python scraper.py --stores "salidzini.lv,pigu.lt" --no-digest
```

Notes:
- If the PC is off at a trigger time, that slot is simply missed (`StartWhenAvailable` catches up at next logon) — GitHub still covers the other stores.
- The offset (…:15 vs …:00) keeps the two runners from writing the gist at the same moment.
- Local runs send the same alerts; the daily digest is sent only by GitHub Actions (`--no-digest` locally).

## Verify it works

- Actions → **Price tracker**: latest run green, "Run tracker" log shows `OK <product-id>: <price> …` lines.
- Telegram: `/prices` shows live prices for UGREEN LP923 and JBL Flip 7 across all tracked stores.
- After a few runs: `/chart jbl` returns a price-history PNG.
- Local task: `Start-ScheduledTask -TaskName 'Cenu Mednieks local tracker'` → salidzini/pigu rows update in `/prices`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| First run exits with `GIST_ID=…` banner | expected once — add the secret (Step 6.1) and re-run |
| `FAIL amazon.de … HTTP 503` | Amazon blocked the run — the Playwright fallback should kick in; if it persists, check the log for the captcha marker |
| `price not found on page` | selectors drifted — run the `scraper-doctor` agent on that entry |
| Schedule didn't fire at exact time | GitHub schedules can be delayed 3–15 min; also confirm the repo has activity (any push) within the last 60 days |
| Commands not answered | they're processed on the next run — trigger **Run workflow**, or check `bot_state.json` in the gist |
