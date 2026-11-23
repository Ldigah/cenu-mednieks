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
| **kurpirkt.lv** | all Latvian shops | ✅ preset + seeded (added 2026-11-18 evening). Cloudflare challenges plain requests everywhere (home IP incl.); Playwright passes from home ~8/9. Microdata `.precebloks` rows + query-token + accessory filters; JBL URL needs `&p1=50` floor. Home-IP runner |
| **ceno.lv** | Latvian shops (780+) | ✅ preset + seeded (added 2026-11-18 evening). Requests + browser UA works 10/10 from home; datacenter IPs challenged even via Playwright → home-IP runner. Microdata `meta[itemprop=price]@content`; min of offers, never lowPrice |
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

---

# Rework — the 20 questions (2026-11-19)

The 2026-11-18 decisions above were revisited after 20 follow-up questions. Superseded
rows are marked; everything else stands.

| # | Question (short) | Decision |
|---|---|---|
| R1 | Main goal | **Deal-catching + best price at buy time** (1+2 combined) |
| R2 | Command latency | **Instant (~1 min)** → Cloudflare Worker webhook + `repository_dispatch` (home PC ruled out: not always on) |
| R3 | Tracking model | **Product-centric**: `/add <name>` → search-based across aggregators (v2 catalog schema; v1 auto-migrates) |
| R4 | Users | Me + family/friends → Mini App + inline queries; alerts stay in the owner chat |
| R5 | Drop sensitivity | **Per-product thresholds** (default 5%, `/threshold`) — supersedes Q8's global 1% |
| R6 | Targets | **All-time-low alerts instead** of target prices (supersedes Q9) |
| R7 | Increases | still silent |
| R8 | Digest | **Enriched**: best store, ATL, Δ7d per product |
| R9 | History/stats | Raw + derived stats (atl, min7/min30, avg30 in `catalog.product_stats`) |
| R10 | New commands | /stats /best /where /threshold /add-url (plus phase-1: /where added early) |
| R11 | Charts | min-price line + per-store lines + ATL marker, ~90-day window |
| R12 | Web presence | **Telegram Mini App** = static GitHub Pages snapshot (publish.py) |
| R13 | State races | **Gist lock + merge-on-write** (lock.json + merge_prices/products/bot_state) |
| R14 | Repo visibility | **Public** — unlimited free Actions minutes + Pages hosting (supersedes Q14) |
| R15 | Code health | ruff + mypy in CI; stores.py split into `stores/` package |
| R16 | Store scope | no new stores for now; pigu stays paused (Q3/Q4 sites unchanged) |
| R17 | Budget | **Always $0** — ScraperAPI free tier (1,000 credits/mo): salidzini+ceno plain 3×/day, kurpirkt rendered 1×/day (daily_only flag) ≈ 480 credits/mo; home PC retired (`setup-local-task.ps1` deleted) |
| R18 | Instant mechanism | Cloudflare Worker (worker/): read-only cmds answered from a 60 s KV cache of the gist; mutating cmds dispatch `command-run` |
| R19 | Surfaces | Mini App via bot menu button → Pages URL; inline queries answered by the Worker |
| R20 | Failure alerts | re-nag every 5th fail after the initial 3-strike alert |

---

# Multi-user & UX — the 40 questions (2026-11-19)

Phase-1 implemented same day (users.py, buttons/callbacks in telegram_io.py, routing in
alerts.py, Worker callbacks). Superseded rows marked. Everything else stands.

| # | Question (short) | Decision |
|---|---|---|
| 1 | Who talks to the bot | **Approved users only** (supersedes nothing; strangers get the Q35 funnel) |
| 2 | Friend adds a product | **One shared catalog** — scraping cost shared |
| 3 | What alerts whom | **Per-user watchlist** (⭐ watch/unwatch); owner starts with watch-all |
| 4 | Alert delivery | **Personal DMs only** (no group) |
| 5 | Join flow | **Owner approves** — [Request access] → Approve/Decline buttons |
| 6 | Roles | **Owner + member** |
| 7 | Quota | **Soft cap 10/member** (owner exempt; per-user quota field) |
| 8 | User data | **Full profile** in users.json (chat id, name, role, watchlist, quota, prefs) |
| 9 | Mini App scope | **Browse + watch** (mutations stay in chat pipeline) |
| 10 | App auth | **Telegram initData HMAC** (phase 2 — app build) |
| 11 | App tech | **Light JS app** (static + app.js + Worker actions; no framework) |
| 12 | App theme | **Follow Telegram themeParams** |
| 13 | Add flow in app | **Chat-first handoff** (button pre-fills /add in chat) |
| 14 | Product detail page | **Full detail card** (chart, stores, ATL, threshold, star, share) |
| 15 | App action buttons | ⭐ watch, 🔔 mute, 🎚 threshold, 🗑 remove (with confirm) |
| 16 | Language | **English now, i18n dict ready; LV later** |
| 17 | New-member defaults | **Silent until they watch something** |
| 18 | Alert format | **Rich card + buttons** [Where] [Chart] [Watch] [Mute] |
| 19 | Quiet hours | **Per-user schedule** (off / 23–08 UTC wrap; held alerts flush next run) |
| 20 | Digest | **Personal, watchlist-only**, at the user's run hour (5/13/21 UTC) |
| 21 | Target prices | **Yes — alongside thresholds** (phase 2) |
| 22 | ATL buy signal | **"% above ATL" everywhere** (implemented: /prices, /stats, inline) |
| 23 | Extra stats | Volatility, best day-of-week, drop frequency (**phase 2**) |
| 24 | Organize products | **Tags** (#audio; /list filter) (**phase 2**) |
| 25 | Deals feed | **Ranked top drops** on the web/app (**phase 2**) |
| 26 | Sharing | **Deep link** t.me/<bot>/<app>?start=<id> (**phase 2, with app**) |
| 27 | /search | **Yes — one-off sweep + [Track] buttons**, rate-limited (**phase 2**) |
| 28 | Inline upgrade | **Buttons + image thumbs** (**phase 2**) |
| 29 | Navigation | **Menu button (Mini App) + rich /help** + BotFather command list |
| 30 | Guided /add | **Pick from matches** — search-page candidates as buttons (implemented) |
| 31 | Confirmations | **/remove, threshold changes, /check** (confirmed flag via Worker) |
| 32 | /settings | **Full per-user panel** (digest, hour, quiet, language) — implemented |
| 33 | Public page policy | **Public but anonymous** — names/prices only, never user data |
| 34 | Rate limits | **Quota + 5-min dispatch cooldown** (Worker KV; confirm-callbacks exempt) |
| 35 | Strangers | **Friendly intro + [Request access]** → pending record + owner ping |
| 36 | User storage | **users.json in the secret gist** (same merge_write path) |
| 37 | Worker 401s | **Verified fixed** (gist/GH tokens 200, webhook healthy); added /ping self-test |
| 38 | Budget | **Track + warn at 80%** of 1,000 credits (bot_state.usage; digest line) |
| 39 | First | **Multi-user core + chat UX** (this phase) |
| 40 | Session scope | **Implement phase 1**; phases 2–3 below |

## Roadmap

- **Phase 1 (done 2026-11-19)**: users.json + approval flow, watchlists, per-user
  alert routing + quiet hours + personal digests, rich alert cards, confirmations,
  guided /add, /settings, quota, Worker callbacks/cooldown//ping, budget tracking.
- **Phase 2 (implemented 2026-11-19)**: Mini App v2 (app/ + publish.py: light JS,
  initData HMAC auth via Worker /api/me|toggle|cmd, watch/mute stars, threshold/
  target editors, deals feed, 90-day chart PNGs, deep links ?p=<id>), /search with
  [Track] (5/day via KV), target prices (/target, hit alerts with re-arm), tags
  (/tag /untag /tags, /list #filter), inline buttons+thumbs (product images via
  JSON-LD/og:image harvest), derived stats (volatility, best weekday, drops30),
  cf:<key> short-key confirmations (64-byte-safe), i18n started (i18n.py en+lv for
  alerts/digest/intro; /help + command acks English until phase 3).
- **Phase 3 (implemented 2026-11-19)**: commands registered via setMyCommands every
  run (default scope + owner chat scope — self-healing; menu button stays the Mini
  App by Telegram design, commands live behind "/" and the input ☰); full LV
  translation end-to-end (/help HELP_LV + watch/mute/target/remove/threshold/quota
  acks via i18n.tr per user prefs); deals.json + deals.xml RSS on Pages (top drops
  vs 30-day avg + ATL); app "+ Track in chat" handoff button (Q13) + RSS footer link.

## Deploy checklist for phase 1

1. `cd worker && npx wrangler deploy`
2. Re-run setWebhook with `allowed_updates='["message","inline_query","callback_query"]'`
   (buttons don't arrive without it).
3. BotFather: `/setcommands` (list in README) + `/setmenubutton` → Mini App URL.
4. Push → CI runs pytest/ruff/mypy; the run migrates the owner into users.json
   automatically (from TELEGRAM_CHAT_ID / the adopted bot_state.chat_id).

## Health-check fixes (2026-11-23)

The weekly report listed 5 of 9 offers failing. They were four unrelated problems:

| Symptom | Cause | Fix |
|---|---|---|
| amazon.pl failing ~half the runs | always a bare `wait_for_selector` timeout (30 of 30 Amazon failures). Reproduced cause: after clicking through Amazon's bot wall, Playwright is left on the storefront root | click through, then re-navigate to the deep link; `api_fallback` on the preset rescues a still-blocked attempt with one plain ScraperAPI fetch (5 credits) |
| salidzini.lv failing on ~1 call in 4 | an LV exit still returns the Turnstile page as HTTP 200 | `fetch_scraperapi` retries on a fresh exit (up to 2×) |
| `lenovo thinkpad t16 gen4` never had a price | bad query: `gen4` matched nothing on ceno.lv, and the `lenovo thinkpad t16 …` form resolves to salidzini's hCaptcha-walled group page | live catalog only — offers repointed at `ThinkPad T16 Gen 4` (salidzini: `… 16GB` to keep accessories out) |
| false "new best" alerts (found on the way, not reported) | the drop alert compares against last run's best. Whenever a run is partial — the cheapest source failed, or the daily cadence skipped it — the dearer store that stands in becomes the reference: the source's unchanged return read as a drop (JBL: 73 € after a run recorded at 77 € = −5.2%), and a smaller real drop cleared the threshold against it | compare like with like: the lowest last-seen price among the offers scraped in this run (`catalog.previous_best`) |

Decisions taken along the way:

- **ScraperAPI is not 1 credit per request — R17 was off by 10×.** The official rate
  table: 10 credits per page behind bot protection (both LV aggregators), 5 for Amazon,
  1 for an ordinary site, +10 rendered. Only 2xx responses are billed, so a challenge
  page returned as 200 is a billed 10. Confirmed against the account counter with
  single-call measurements. At three products the scheduled runs alone cost ≈60 credits
  a run ≈ 180 a day, more than the account's 5,000-a-month limit.
- **Aggregators once a day** (the owner's decision, same day). `daily_only` on both
  presets: whichever run is first in the UTC day scrapes them, the other two skip.
  Six offers × 10 = 60 credits a day instead of 180 — ≈90 a day ≈ 2,700 a month with
  retries and the Amazon rescue. Amazon and hinnavaatlus are on the free ladder and
  still run 3×/day; a price drop on an LV shop is noticed up to 24 h later instead
  of 8 h. It makes partial runs routine, which is why the drop alert's reference had
  to become like for like (above).
- **`usage` counts credits, not calls.** Each response states its own cost in the
  `sa-credit-cost` header; the counter behind the digest line and the 80% warning adds
  that. November's figure under-reports the calls made before the switch (all of them
  10-credit pages); it is exact from the December reset.
- **Rescue, don't replace.** Amazon.pl stays Playwright-first (free) and pays 5 credits
  only when that fails — ≈15 a day at the observed failure rate, less if the wall fix
  holds on a runner.
- **Retry a flagged exit.** The failed page is billed either way, so one more call costs
  nothing per successful page (≈13.7 credits per success with or without the retry); it
  just stops paid-for failures from being thrown away. ≈10 a day at the measured
  1-in-4 rate.
- **No generic accessory filter.** salidzini's search page is the ~40 cheapest matches, so
  a laptop query's minimum was a 22 € screen protector. A category blocklist can't be
  right in general — the LP923 *is* an accessory — and six polluting rows had a blank
  category anyway. The query carries the discrimination for now.
- **The weekly check probes for real.** It used to run without `SCRAPERAPI_KEY` and skip
  the aggregators, so it replayed stale counters, re-tested Amazon on a path the
  scheduled runs don't use, and recorded a partial best as the product's last minimum
  (another source of false drops). It now has the key and probes on the scheduled
  runs' path; under the daily cadence it normally reports the 05:00 run's aggregator
  results an hour later, and takes the day's scrape itself only if that run was delayed.
- **Failures say why.** Each offer stores `last_error` (cleared on success) and the weekly
  report prints it. `fetch_playwright` errors name the page they were left on.
- **Budget ceiling left alone.** The account API reported `requestLimit: 5000`;
  `SCRAPERAPI_MONTHLY_CREDITS` still says 1,000, so the warning errs early. Which one is
  the real ceiling is the owner's decision.
- **Not verified on a GitHub runner at the time of writing:** Amazon's wall path (it does
  not appear from a home IP). It is reproduced by a local stand-in that yields the
  identical error signature when the fix is removed.

