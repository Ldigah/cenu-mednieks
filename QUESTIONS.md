# 30 Questions Before Implementation

> ✅ **Answered 2026-11-18** — every answer is recorded in [DECISIONS.md](DECISIONS.md). This file is kept for reference.

Answer inline next to each question (e.g. `1. Electronics, ~10 products`) or in chat by number.
Where a **default** is given, staying silent on that question means "use the default".

## A. Products & scope

1. Which products do you want to track first, and roughly how many? *(Default: start with 3–5, expand later.)*
2. Which Latvian stores matter? 220.lv, 1a.lv, ksenukai.lv, rde.lv, elkor.lv, dateks.lv, others? *(Default: whichever stores the chosen products are sold at.)*
3. Are you open to scraping **salidzini.lv** (price aggregator) instead of each Latvian shop individually — one stable layout, minimum price across all stores at once? *(Default: yes for Latvian shops where possible.)*
4. Only Amazon.de, or also other foreign shops (Amazon.pl, pigu.lt, …)? *(Default: Amazon.de only.)*
5. Track availability/restock alerts too, or strictly price + discount? *(Default: availability too.)*
6. Do you need product variants (colors/sizes/storage) handled, or is one URL per product enough? *(Default: one URL per product.)*

## B. Alerting

7. When should Telegram message you: (a) every run, 3×/day; (b) only when something changes; (c) only price drops / new discounts / target hits? *(Default: b, plus one daily digest.)*
8. What minimum change triggers an alert — any drop, or ≥1% / ≥5% / ≥10%? *(Default: any drop ≥ 1%.)*
9. Do you want per-product **target prices** ("alert me at ≤ €199")? *(Default: yes, optional field in products.yaml.)*
10. Should price **increases** also be alerted, or recorded silently? *(Default: recorded silently.)*
11. Want a fixed daily summary message (all products, current prices) in addition to event alerts? *(Default: yes, attached to the 23:00 run.)*
12. Message language English or Latvian? Compact one-liners or detailed (old→new price, discount %, link)? *(Default: English, compact with old→new price and link.)*

## C. Schedule & infrastructure

13. Exact run times in Latvian local time — is 07:00/15:00/23:00 right? (Cron is UTC; GitHub has no DST, so summer runs shift an hour earlier local — acceptable?) *(Default: yes, 07/15/23 local.)*
14. Repository **private** or public? Private = limited free Actions minutes (this bot uses ~10% of them) but everything hidden; public = unlimited minutes but code + price history visible. *(Default: private.)*
15. OK to commit the price history (`state/prices.json`) into the repo? *(Default: yes — it also keeps the schedule from being auto-disabled.)*
16. Strictly $0, or open to a scraping-API free tier (ScraperAPI/Zenrows, ~1k free calls/month) if Amazon blocks GitHub's IPs? *(Default: free-first, decide when it actually breaks.)*
17. Should the bot also run on manual trigger (Actions UI) and automatically whenever `products.yaml` changes? *(Default: yes to both.)*
18. Max acceptable runtime per run? (Affects whether Playwright is viable.) *(Default: ≤ 5 minutes.)*

## D. Scraping technology

19. How critical is **Amazon.de** — must-work on day one, or nice-to-have? (This single answer drives most of the technical complexity.)
20. If needed for Amazon: OK to add Playwright (headless browser — slower runs, bigger install, still free)? *(Default: yes, but only for stores that need it.)*
21. When a scrape fails, alert immediately or only after N consecutive failures (avoids noise from one-off glitches)? *(Default: after 3.)*
22. Prefer JSON-LD structured data whenever a store provides it (more stable than CSS selectors)? *(Default: yes, always JSON-LD first.)*
23. Are all prices EUR? If a store shows another currency, convert, flag, or skip? *(Default: all EUR; flag anything else as ⚠️.)*
24. Any unusual price formats expected beyond "12,99 €", "1 299 €", superscript cents? *(Default: parser handles those three.)*

## E. Config & future UX

25. How do you want to add products later: edit `products.yaml` in the repo, Telegram commands (`/add <url>`), or eventually a web UI? *(Default: YAML now, Telegram commands as phase 2.)*
26. Want Telegram commands (`/prices`, `/list`) to query current prices on demand? *(Default: later phase.)*
27. Do you eventually want the dashboard on `bot.yourdomain.com` (static HTML charts from the committed price history), or skip? *(Default: later, static page.)*
28. Alerts go to your personal chat or a group? Any other channels ever (email, Discord)? *(Default: personal chat.)*

## F. Maintenance & legal

29. Just you maintaining this? Want a weekly "selector health check" run plus minimal tests for the parsing code? *(Default: yes to both.)*
30. Are you comfortable with light personal-use scraping of these stores (3×/day, one page per product)? Amazon's ToS is the strictest — is the Keepa API an acceptable fallback there? *(Default: proceed with scraping; Keepa as fallback.)*
