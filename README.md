# Cenu Mednieks 🛒

Price & discount tracker for Latvian, Baltic and EU shops — **fully autonomous** on the
free tier: GitHub Actions scrapes 3×/day, alerts land in Telegram, a public price page
publishes to GitHub Pages, and an optional Cloudflare Worker answers commands instantly.

- **Product-centric**: `/add JBL Flip 7` tracks the product across salidzini.lv,
  kurpirkt.lv and ceno.lv at once; attach Amazon or direct shops with `/add_url`.
- **Deal alerts**: best-price drops (per-product threshold, default 5%), all-time lows,
  new/deeper discounts, restocks. Increases recorded silently. Daily digest at 23:00
  Latvia time with best store, ATL and 7-day delta per product.
- **Stores**: salidzini.lv + ceno.lv (all LV shops; via ScraperAPI's free tier in CI with
  LV geo-pinning — Cloudflare blocks datacenter IPs), hinnavaatlus.ee, amazon.pl (PLN→EUR;
  amazon.de drops the buybox to datacenter IPs), plus any shop with JSON-LD product data.
  kurpirkt.lv works from residential IPs only (ScraperAPI can't fetch it).
- **Surfaces**: Telegram chat (owner alerts + commands), Telegram Mini App / public
  page (`index.html` from [publish.py](publish.py)), inline queries via the Worker.
- **Multi-user** (2026-11-19): approved members share one catalog; each has their own
  watchlist, quota (10), quiet hours and personal digest. Strangers get a
  [Request access] button; the owner approves with one tap. See DECISIONS.md.
- **Phase 2**: Mini App v2 (Telegram-themed, per-user watch/targets via initData
  auth, deals feed, charts), target prices (🎯 ping when under X), tags,
  volatility/weekday/drop-frequency stats, /search, inline cards with buttons.
- **State** lives in a secret GitHub Gist (`products.json`, `prices.json`,
  `bot_state.json`, `users.json`) — nothing is committed back. Overlapping runs are
  safe: advisory gist lock + merge-on-write.

**→ Setup guide: [SETUP.md](SETUP.md)** · Decisions: [DECISIONS.md](DECISIONS.md) ·
Worker: [worker/README.md](worker/README.md)

```text
  Telegram cmd / Mini App / inline          cron 3×/day + repository_dispatch
        │                                          │
        ▼                                          ▼
  Cloudflare Worker  ──reads──► state gist ◄──writes── GitHub Actions (scraper.py)
  (instant read-only                              │
   cmds; dispatches runs)                         ▼
                                        product → offers → adapters
                                        (salidzini / kurpirkt / ceno / amazon / …)
                                                 │
                                 GitHub Pages ◄──┘  Telegram alerts · /chart PNGs · digest
```

## Commands

`/add <name>` (guided: pick from matches) · `/add <url> [name]` · `/add_url <url> [part]` ·
`/watch [part]` · `/unwatch <part>` · `/mute <part>` · `/unmute <part>` ·
`/target <part> <price|off>` · `/threshold <part> <pct>` · `/tag <part> #t` ·
`/untag <part> #t` · `/tags` · `/search <name>` (5/day) · `/list [#tag]` · `/prices` ·
`/where <part>` · `/stats [part]` · `/best` · `/chart [part]` · `/remove <part>` ·
`/check` · `/settings` · `/requests` · `/quota` · `/whoami` · `/watchall` · `/help`

Alert cards carry buttons: [Where] [Chart] [Watch] [Mute]; destructive actions
(/remove, threshold changes, /check) ask for confirmation first.

## Local development

```bash
pip install -r requirements-dev.txt
pytest -q                                  # unit tests (no network)
ruff check . && mypy .                     # lint + types
python scraper.py --local --only jbl       # dry run, state in state-local/, no Telegram
python publish.py --out site --local       # render the web snapshot locally
```

CI runs pytest + ruff + mypy on every push (see [.github/workflows/tests.yml](.github/workflows/tests.yml)).
The Cloudflare-guarded LV aggregators use ScraperAPI in CI, as does the single rescue fetch
for an Amazon.pl attempt that Playwright couldn't get through (`SCRAPERAPI_KEY` secret).
ScraperAPI bills those at 10 and 5 credits respectively — ≈60 per run, ≈180 a day with
three products; without the key those stores fall back to the direct requests→Playwright
ladder, which works from residential IPs.
