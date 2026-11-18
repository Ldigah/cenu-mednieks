# Cenu Mednieks 🛒

Price & discount tracker for Latvian shops and Amazon — runs **3×/day for free** on GitHub Actions and alerts via Telegram.

- Tracks any product page on [salidzini.lv](https://www.salidzini.lv) (min price across all LV shops), [hinnavaatlus.ee](https://www.hinnavaatlus.ee) (EE shops), Amazon.de, Amazon.pl (PLN→EUR), pigu.lt, and any other shop with JSON-LD product data.
- Alerts only on change: price drops ≥ 1%, new/deeper discounts, restocks. Daily digest at 23:00 Latvia time.
- Managed entirely from Telegram: `/add <url> [name]`, `/list`, `/prices`, `/chart`, `/remove`.
- Price history + product list live in a **private GitHub Gist** — nothing is ever committed back to the repo.

**→ Setup guide: [SETUP.md](SETUP.md)** · Blueprint: [PLAN.md](PLAN.md) · Decisions: [DECISIONS.md](DECISIONS.md)

```text
Telegram commands → gist (products.json)      cron 3×/day (GitHub Actions)
                        │                            │
                        ▼                            ▼
                  scraper.py ──► store adapters ──► salidzini.lv / amazon.de / amazon.pl / pigu.lt
                        │              (JSON-LD first, CSS fallback, Playwright when blocked)
                        ▼
                 gist (prices.json) ──► Telegram alerts, /chart PNGs, daily digest
```

## Local development

```bash
pip install -r requirements-dev.txt
pytest -q                      # unit tests (no network)
python scraper.py --local --only jbl    # dry run, state in state-local/, no Telegram
```
