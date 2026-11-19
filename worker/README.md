# Cloudflare Worker — instant Telegram commands

The GitHub Actions run processes commands only 3×/day. This Worker is the
always-on piece (free tier: 100k requests/day): it receives the Telegram
webhook, answers **read-only** commands instantly from a 60-second-cached copy
of the state gist, and triggers a **full Actions run** for mutating commands
via `repository_dispatch` (the run then processes them with the real bot
logic — `/add`, `/remove`, `/threshold`, `/chart`, `/check` answer in a few
minutes instead of hours). Inline queries (`@yourbot <name>`) also work.

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

Point Telegram at the Worker (once):

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d url="https://cenu-mednieks-bot.<your-subdomain>.workers.dev" \
  -d secret_token="<WEBHOOK_SECRET>" \
  -d allowed_updates='["message","inline_query"]'
```

**GH_TOKEN**: classic PAT with `repo` scope, or a fine-grained PAT with
*Contents: read & write* on this repository (needed for `repository_dispatch`).
Keep it distinct from `GIST_TOKEN` (which only needs the `gist` scope).

## Verify

- Telegram: `/prices` → instant reply.
- `/add <name>` → "Queued a full run" → Actions tab shows a `command-run` run → bot replies in ~2–5 min.
- In any chat: type `@yourbot jbl` → price card appears.

## Notes

- The webhook secret header (`X-Telegram-Bot-Api-Secret-Token`) is checked on
  every request — anyone else posting gets a 403.
- Read-only answers are text-only approximations of the bot's HTML replies;
  charts still arrive from the Actions run (`/chart` queues one).
- Deleting the webhook (`/deleteWebhook`) reverts everything to the 3×/day
  schedule with zero config left behind.
