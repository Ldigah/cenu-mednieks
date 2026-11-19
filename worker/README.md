# Cloudflare Worker — instant Telegram commands

The GitHub Actions run processes commands only 3×/day. This Worker is the
always-on piece (free tier: 100k requests/day): it receives the Telegram
webhook, answers **read-only** commands instantly from a 60-second-cached copy
of the state gist, handles **button taps** (approvals, watch/mute, settings —
direct users.json edits; charts/removes — confirmed dispatches), and triggers
a **full Actions run** for mutating commands via `repository_dispatch` (the
run processes them with the real bot logic). Inline queries (`@yourbot <name>`)
also work.

## Deploy (once, ~5 minutes)

```bash
cd worker
npx wrangler login
npx wrangler kv namespace create CACHE     # copy the printed id into wrangler.toml
```

Fill `GIST_ID` and `REPO` in [wrangler.toml](wrangler.toml), then set secrets:

```bash
npx wrangler secret put TELEGRAM_BOT_TOKEN   # from BotFather
npx wrangler secret put GIST_TOKEN           # the same gist-scope PAT as CI
npx wrangler secret put GH_TOKEN             # PAT with repo scope (repository_dispatch)
npx wrangler secret put WEBHOOK_SECRET       # any long random string
npx wrangler deploy
```

Point Telegram at the Worker (once — include `callback_query` or buttons won't arrive):

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d url="https://cenu-mednieks-bot.<your-subdomain>.workers.dev" \
  -d secret_token="<WEBHOOK_SECRET>" \
  -d allowed_updates='["message","inline_query","callback_query"]'
```

**GH_TOKEN**: classic PAT with `repo` scope, or a fine-grained PAT with
*Contents: read & write* on this repository (needed for `repository_dispatch`).
Keep it distinct from `GIST_TOKEN` (which only needs the `gist` scope — but
must ALSO be able to write the gist: the Worker writes users.json for
approvals/watch/mute/settings).


## Command flow (important)

With a webhook active, Telegram forbids `getUpdates` (HTTP 409) — so CI cannot
poll for commands. The flow is therefore:

- **Read-only** (`/prices /where /stats /best /list /ping`): answered by the
  Worker instantly from the cached gist.
- **Mutating** (`/add /add-url /threshold /remove /chart /check /pick`): the
  Worker gates them (membership via users.json, quota, 5-min KV cooldown for
  members, /check owner-only) and dispatches `command-run` **with the command
  text in `client_payload.commands`** (plus `confirmed: true` when it came
  from a button, so CI skips re-confirmation). See `payload_updates` in
  `telegram_io.py` and the `GITHUB_EVENT_PATH` block in `scraper.py`.
- **Button taps** (callback_query): approvals/declines, watch/mute toggles and
  settings rows are written **directly to users.json in the gist** (read-merge-
  write, cache busted after). [Chart]/[Remove]/[Threshold] confirmations and
  guided-add picks dispatch a confirmed run instead.
- **Strangers**: any unknown chat gets an intro + [Request access]; the tap
  creates a pending users.json record and pings the owner with ✅/❌ buttons.

Inline queries must be enabled once in BotFather (`/setinline`) or Telegram sends
`@bot query` as a normal message instead of an inline query.

## Mini App API (phase 2)

`POST /api/me` `{initData}` → per-user view (role, watchlist, muted, targets,
quota_left). `POST /api/toggle` `{initData, action: "watch"|"mute", pid}` →
direct users.json write, returns `{ok, on}`. `POST /api/cmd` `{initData,
cmd: "threshold"|"target"|"remove", pid, pct?|price?}` → confirmed dispatch to
CI. All three verify the Telegram initData HMAC against the bot token (24 h
freshness), gate on membership, and answer CORS only to the Pages origin.

## Verify

- Telegram: `/prices` → instant reply.
- `/ping` (owner) → end-to-end self-test: gist fetch, GH dispatch token, Telegram send.
- `/add <name>` → "Queued a full run" → Actions tab shows a `command-run` run → bot replies in ~2–5 min.
- In any chat: type `@yourbot jbl` → price card appears.
- From a friend's account: `/prices` → intro + [Request access] → owner gets ✅/❌.

## Notes

- The webhook secret header (`X-Telegram-Bot-Api-Secret-Token`) is checked on
  every request — anyone else posting gets a 403.
- Read-only answers are text-only approximations of the bot's HTML replies;
  charts still arrive from the Actions run.
- Rate limit (Q34): members may trigger 1 run per 5 minutes (KV `cd:<chat_id>`,
  TTL 300); button-confirmation dispatches are exempt; the owner is exempt.
- Deleting the webhook (`/deleteWebhook`) reverts everything to the 3×/day
  schedule with zero config left behind (Python then polls updates incl.
  callback_query and handles the same flows itself).
