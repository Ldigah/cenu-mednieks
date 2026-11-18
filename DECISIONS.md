# DECISIONS — answers to the 30 questions (2026-11-18)

| # | Question (short) | Decision |
|---|---|---|
| 1 | First products | **UGREEN LP923**, **JBL Flip 7** (seeded) |
| 2 | Stores | wherever the products sell — via aggregators + direct |
| 3 | salidzini.lv | **Yes** + other aggregators (see below) |
| 4 | Foreign shops | **All**: Amazon.de, Amazon.pl, pigu.lt, … |
| 5 | Availability | include (restock alerts ✅) |
| 6 | Variants | one URL per product |
| 7 | Alerting | changes only + daily digest |
| 8 | Threshold | any drop ≥ 1% |
| 9 | Target prices | **none** |
| 10 | Increases | silent (recorded) |
| 11 | Daily digest | yes, 23:00 Latvia run |
| 12 | Message style | English, compact, old→new price |
| 13 | Times | 07:00/15:00/23:00 Latvia (cron `0 5,13,21 * * *` UTC) |
| 14 | Repo | private |
| 15 | State in repo | **No** → stored in a secret GitHub Gist instead |
| 16 | Budget | free only (no scraping APIs) |
| 17 | Triggers | schedule + manual `workflow_dispatch` |
| 18 | Runtime | ≤ 5 min (Playwright cached, within budget) |
| 19 | Amazon.de | **must work day one** → requests + Playwright fallback built in |
| 20 | Playwright | yes, only where needed |
| 21 | Failure alerts | after 3 consecutive failures |
| 22 | JSON-LD | always first choice |
| 23 | Currency | EUR; anything else flagged ⚠️ |
| 24 | Price formats | "12,99 €", "1 299 €", superscript cents handled by parser |
| 25 | Adding products | **Telegram `/add` now** (processed each run) |
| 26 | Telegram commands | **now**: /add /list /prices /chart /check /remove /help |
| 27 | Dashboard | **in the bot**: `/chart` sends price-history PNGs |
| 28 | Delivery | personal chat |
| 29 | Maintenance | solo + weekly health-check workflow + pytest |
| 30 | Scraping stance | light personal-use OK; Keepa as Amazon fallback if ever needed |

## Aggregators beyond salidzini.lv (Q3 follow-up)

Live-researched 2026-11-18 by the selector-finder agents:

| Aggregator | Covers | Status |
|---|---|---|
| **salidzini.lv** | all Latvian shops | ✅ preset + seeded. `/search?q=` URLs only (slug URLs → hCaptcha); AggregateOffer JSON-LD; no discount data exists on the site |
| **hinnavaatlus.ee** | Estonian shops (incl. Baltic chains) | ✅ preset + seeded (JBL). Static HTML, `.offer-price`, verified min 89.65 € / 41 offers. LP923 not listed there |
| **pigu.lt** | LT marketplace (group also runs 220.lv, ksenukai.lt, kaup24.ee) | ⏸ paused 2026-11-18: Cloudflare flagged both GitHub's and the home IP after heavy retries; JSON-LD preset stays in stores.py — re-add via `/add` once it cools down |
| kainos.lt | Lithuania | ❌ blocked (robot-check page) — Playwright tier only, add later via selector-finder |
| Ceneo.pl | Poland | ❌ soft-blocked (JS shell) — Playwright tier |
| idealo.de | Germany/EU | ❌ blocked (Akamai 403) — Playwright tier |
| geizhals.de | EU electronics | not checked — Playwright tier likely |

Keepa (Amazon price history) is API-paid → excluded by Q16 unless everything else fails (Q30 allows it as fallback).

Direct Latvian shop pages (220.lv, 1a.lv, ksenukai.lt, …) need no presets: the auto adapter reads their JSON-LD, and salidzini already aggregates them.

## Live research snapshot (2026-11-18)

| Product / source | Price found |
|---|---|
| UGREEN LP923 · salidzini.lv | **12.45 €** min (eltek.lv), 13 offers — it's a **MagSafe car phone holder**, not a laptop stand (confirmed on 3 sites independently) |
| UGREEN LP923 · pigu.lt | 19.99 € (PLUS loyalty 10.49 €) |
| JBL Flip 7 · salidzini.lv | **73.00 €** min (24.lv) after filtering out battery/accumulator accessories (raw min was 19.85 € — a Flip 4 replacement battery) |
| JBL Flip 7 · pigu.lt | 115.39 € (PLUS 94.49 €) |
| JBL Flip 7 · hinnavaatlus.ee | 89.65 € min across 41 offers |
| Amazon.de/.pl · both products | not obtainable from this IP (captcha even on residential) — Playwright in CI; selectors documented, medium confidence until first live run |
