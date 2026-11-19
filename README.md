# Cenu Mednieks 🛒

Price & discount tracker for Latvian, Baltic and EU shops — **fully autonomous** on the
free tier: GitHub Actions scrapes 3×/day, alerts land in Telegram, a public price page
publishes to GitHub Pages, and an optional Cloudflare Worker answers commands instantly.

- **Product-centric**: `/add JBL Flip 7` tracks the product across salidzini.lv,
  kurpirkt.lv and ceno.lv at once; attach Amazon or direct shops with `/add-url`.
- **Deal alerts**: best-price drops (per-product threshold, default 5%), all-time lows,
  new/deeper discounts, restocks. Increases recorded silently. Daily digest at 23:00
  Latvia time with best store, ATL and 7-day delta per product.
- **Stores**: salidzini.lv + ceno.lv (all LV shops; via ScraperAPI's free tier in CI with
  LV geo-pinning — Cloudflare blocks datacenter IPs), hinnavaatlus.ee, amazon.pl (PLN→EUR;
  amazon.de drops the buybox to datacenter IPs), plus any shop with JSON-LD product data.
  kurpirkt.lv works from residential IPs only (ScraperAPI can't fetch it).
- **Surfaces**: Telegram chat (owner alerts + commands), Telegram Mini App / public
  page (`index.html` from [publish.py](publish.py)), inline queries via the Worker.
- **State** lives in a secret GitHub Gist — nothing is committed back. Overlapping
  runs are safe: advisory gist lock + merge-on-write.

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

`/add <name>` · `/add <url> [name]` · `/add-url <url> [part]` · `/threshold <part> <pct>` ·
`/list` · `/prices` · `/where <part>` · `/stats [part]` · `/best` · `/chart [part]` ·
`/remove <part>` · `/check` · `/help`

## Local development

```bash
pip install -r requirements-dev.txt
pytest -q                                  # unit tests (no network)
ruff check . && mypy .                     # lint + types
python scraper.py --local --only jbl       # dry run, state in state-local/, no Telegram
python publish.py --out site --local       # render the web snapshot locally
```

CI runs pytest + ruff + mypy on every push (see [.github/workflows/tests.yml](.github/workflows/tests.yml)).
The Cloudflare-guarded LV aggregators use ScraperAPI in CI (`SCRAPERAPI_KEY` secret,
≈690 of the free 1,000 credits/month); without the key they fall back to the direct
requests→Playwright ladder, which works from residential IPs.
