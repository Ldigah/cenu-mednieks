/**
 * Cenu Mednieks — Telegram webhook receiver (Cloudflare Worker, free tier).
 *
 * Read-only commands (/list /prices /where /stats /best /help /ping) are answered
 * in ~1 s from a 60-second-cached copy of the state gist. Mutating commands
 * (/add /add-url /threshold /remove /chart /check) trigger a full GitHub Actions
 * run via repository_dispatch — the run processes them with the real Python
 * logic (the command text travels in client_payload.commands, optionally with
 * confirmed:true when it came from an inline button).
 *
 * Multi-user (DECISIONS.md 2026-11-19, the 40 questions): membership lives in
 * users.json — approved users only (Q1). Strangers get an intro + [Request
 * access]; the owner approves/declines with one tap (Q5/Q35). Watch/unwatch,
 * mute and settings callbacks write users.json directly (per-record merge,
 * Q15/Q32); alerts themselves are sent by the CI run. Run-triggering commands
 * from members are cooldown-limited in KV (Q34); /check stays owner-only.
 *
 * Inline queries (@bot <name>) get live price cards in any chat.
 */

const API = "https://api.telegram.org";
const READ_ONLY = new Set(["start", "help", "list", "prices", "where", "stats", "best"]);
// Mutating chat commands (dispatch a run; CI enforces quota/permissions again)
const MUTATING = new Set(["add", "add_url", "threshold", "remove", "chart", "check", "pick"]);
const COOLDOWN_S = 300; // Q34: one run-triggering command per member per 5 min

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("cenu-mednieks webhook — POST only", { status: 405 });
    }
    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!secret || secret !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad json", { status: 400 });
    }
    // Telegram needs a fast 200; do the work without blocking the response.
    await handle(update, env); // still awaited — Workers allow up to 30 s of CPU
    return new Response("ok");
  },
};

async function handle(update, env) {
  try {
    if (update.inline_query) return await handleInline(update.inline_query, env);
    if (update.callback_query) return await handleCallback(update.callback_query, env);
    const msg = update.message;
    if (!msg || !msg.text || !msg.text.startsWith("/")) return;
    const [rawCmd, ...rest] = msg.text.slice(1).split(" ");
    const cmd = rawCmd.split("@")[0].toLowerCase().replace(/-/g, "_");
    const arg = rest.join(" ").trim();
    const chatId = msg.chat && msg.chat.id;
    if (!chatId) return;

    const member = await memberOf(env, chatId);

    // Approved users only (Q1): strangers get the request funnel on every command.
    if (!member) return strangerCard(env, chatId, msg);
    if (member.status !== "active") {
      return send(chatId, member.status === "pending"
        ? "⏳ Your access request is waiting for the owner's approval."
        : "⛔ This account is not allowed to use the bot.", env);
    }

    // read-only: instant from the cache
    if (READ_ONLY.has(cmd)) {
      const state = await getState(env);
      if (!state) return send(chatId, "⚠️ State gist unavailable — try again soon.", env);
      return send(chatId, answerReadOnly(cmd, arg, state), env);
    }

    if (cmd === "ping") {
      if (member.role !== "owner") {
        return send(chatId, "⛔ Owner only.", env);
      }
      return send(chatId, await selfTest(env), env);
    }

    if (MUTATING.has(cmd)) {
      const gate = await gateMutation(env, member, cmd, arg, state_of(await getState(env)));
      if (gate) return send(chatId, gate, env);
      const ok = await dispatch(env, msg.text, chatId, false);
      return send(chatId, ok
        ? `🚀 Queued a full run — I'll process /${cmd} and reply in ~2–5 minutes.`
        : "⚠️ Could not trigger a run — check the Worker's GH_TOKEN secret.", env);
    }
    return send(chatId, `Unknown command /${cmd}. Try /help`, env);
  } catch (e) {
    console.error("handle failed:", e);
  }
}

// ---------------------------------------------------------------------------
// Membership (users.json in the gist; OWNER_CHAT_ID as the bootstrap fallback)
// ---------------------------------------------------------------------------

function state_of(state) { return state || { products: { products: [] }, prices: {} }; }

async function memberOf(env, chatId) {
  const state = await getState(env);
  const u = ((state && state.users && state.users.users) || [])
    .find((x) => String(x.chat_id) === String(chatId));
  if (u) return u;
  if (env.OWNER_CHAT_ID && String(chatId) === String(env.OWNER_CHAT_ID)) {
    return { chat_id: chatId, name: "Owner", role: "owner", status: "active",
             watch_all: true, watchlist: [], muted: [],
             prefs: { digest: true, digest_hour_utc: 21, quiet: "off", lang: "en" } };
  }
  return null;
}

function quotaLeft(member, products) {
  if (member.role === "owner") return null;
  const used = (products.products || [])
    .filter((p) => String(p.added_by) === String(member.chat_id)).length;
  return Math.max(0, (member.quota || 10) - used);
}

async function gateMutation(env, member, cmd, arg, state) {
  // Cooldown (Q34): one dispatch per member per 5 min — the owner is trusted.
  if (member.role !== "owner") {
    const cd = await env.CACHE.get(`cd:${member.chat_id}`);
    if (cd) return "⏱ Easy there — one run-triggering command per 5 minutes.";
    await env.CACHE.put(`cd:${member.chat_id}`, String(Date.now()), { expirationTtl: COOLDOWN_S });
  }
  if (cmd === "check") return member.role === "owner"
    ? null : "⛔ Owner only — /check triggers a full paid run.";
  if (cmd === "add" || cmd === "add_url") {
    const q = quotaLeft(member, state.products);
    if (q !== null && q <= 0) return "📦 Quota reached — ask the owner to raise it.";
    return null;
  }
  if (cmd === "remove" || cmd === "threshold") {
    const part = (arg.split(/[ ]+/)[0] || "").toLowerCase();
    const prod = findProduct(state, arg);
    if (prod) {
      const mine = member.role === "owner"
        || String(prod.added_by) === String(member.chat_id)
        || prod.added_by == null;
      if (!mine) return "⛔ Only the owner or whoever added this product can change it.";
    } else if (!part) {
      return "Usage: /" + cmd + " <name-part>";
    }
    return null; // unknown name-parts are handled (with a real reply) by the CI run
  }
  return null;
}

function findProduct(state, arg) {
  const part = (arg || "").toLowerCase().trim();
  const prods = (state.products && state.products.products) || [];
  if (!part) return null;
  return prods.find((p) => `${p.id} ${p.name}`.toLowerCase().includes(part)
    || (p.offers || []).some((o) => o.url.toLowerCase().includes(part))) || null;
}

async function strangerCard(env, chatId, msg) {
  const state = await getState(env);
  const known = ((state && state.users && state.users.users) || [])
    .find((x) => String(x.chat_id) === String(chatId));
  if (known && known.status === "pending") {
    return send(chatId, "⏳ Your access request is waiting for the owner's approval.", env);
  }
  if (known && known.status === "blocked") return; // silently ignore
  const intro = "👋 Hi! I'm Cenu Mednieks — I hunt prices & discounts across Latvian, "
    + "Baltic and EU shops.\n\nAccess is by approval: tap the button and the owner "
    + "will let you in. Once approved you can track products, build a watchlist "
    + "and get deal alerts.";
  await tg("sendMessage", {
    chat_id: chatId, text: intro, disable_web_page_preview: true,
    reply_markup: { inline_keyboard: [[{ text: "🙋 Request access", callback_data: "req" }]] },
  }, env);
  if (!known) {
    const from = msg.from || {};
    await updateUsers(env, (ud) => {
      ud.users.push({ chat_id: chatId, name: from.first_name || "",
                      username: from.username || "", role: "member", status: "pending",
                      watch_all: false, watchlist: [], muted: [], quota: 10,
                      added: new Date().toISOString().slice(0, 19) + "Z",
                      prefs: { digest: false, digest_hour_utc: 21, quiet: "off", lang: "en" } });
      return ud;
    });
    const owner = env.OWNER_CHAT_ID;
    if (owner) {
      await tg("sendMessage", {
        chat_id: owner,
        text: `🙋 ${from.first_name || ""} (@${from.username || "?"}) requests access.`,
        reply_markup: { inline_keyboard: [[
          { text: "✅ Approve", callback_data: `apr:${chatId}` },
          { text: "❌ Decline", callback_data: `dec:${chatId}` },
        ]] },
      }, env);
    }
  }
}

// ---------------------------------------------------------------------------
// Callbacks (Q5/Q15/Q18/Q30/Q31): instant state edits or confirmed dispatches
// ---------------------------------------------------------------------------

async function handleCallback(cb, env) {
  const chatId = cb.message && cb.message.chat && cb.message.chat.id;
  const data = cb.data || "";
  if (!chatId || !data) return;
  const answer = (text) => tg("answerCallbackQuery",
    { callback_query_id: cb.id, text: (text || "").slice(0, 190) }, env);
  if (data === "noop") return answer();

  const member = await memberOf(env, chatId);

  if (data === "req") {
    if (member && member.status === "active") {
      await answer("You're already in!");
      return send(chatId, "You're already in — /help for commands.", env);
    }
    await answer("Request sent!");
    return strangerCard(env, chatId, cb); // creates the pending record + owner ping
  }

  if (!member || member.status !== "active") return answer("Not allowed yet.");

  const [action, rest] = splitCb(data);

  if (action === "apr" || action === "dec") {
    if (member.role !== "owner") return answer("Owner only.");
    const target = rest;
    let ok = false;
    await updateUsers(env, (ud) => {
      const u = (ud.users || []).find((x) => String(x.chat_id) === String(target));
      if (u && u.status === "pending") { u.status = action === "apr" ? "active" : "blocked"; ok = true; }
      return ud;
    });
    if (!ok) return answer("Request is gone.");
    await answer(action === "apr" ? "Approved!" : "Declined.");
    await send(chatId, action === "apr" ? `✅ Approved user ${target}.` : `❌ Declined user ${target}.`, env);
    if (action === "apr") {
      return send(target, "🎉 You're in!\n\nInstant: /prices /best /where /stats /list\n"
        + "Track: /add <name> (pick from matches) · /watch <part> ⭐\n"
        + "You: /settings /quota /whoami", env);
    }
    return send(target, "The owner declined your access request — sorry!", env);
  }

  if (action === "wt" || action === "mu") {
    const prod = findProduct(state_of(await getState(env)), rest);
    if (!prod) return answer("Product not found.");
    let msg = "";
    await updateUsers(env, (ud) => {
      const u = (ud.users || []).find((x) => String(x.chat_id) === String(member.chat_id));
      if (!u) return ud;
      const list = action === "wt" ? (u.watchlist = u.watchlist || [])
                                   : (u.muted = u.muted || []);
      const other = action === "wt" ? (u.muted || []) : (u.watchlist || []);
      const i = list.indexOf(prod.id);
      if (i >= 0) { list.splice(i, 1); msg = action === "wt" ? "💔 Unwatched" : "🔔 Unmuted"; }
      else {
        list.push(prod.id);
        const j = other.indexOf(prod.id);
        if (j >= 0) other.splice(j, 1);
        msg = action === "wt" ? "⭐ Watching" : "🔕 Muted";
      }
      return ud;
    });
    await answer(msg);
    return send(chatId, `${msg} ${prod.name}`, env);
  }

  if (action === "st") {
    let msg = "";
    await updateUsers(env, (ud) => {
      const u = (ud.users || []).find((x) => String(x.chat_id) === String(member.chat_id));
      if (!u) return ud;
      const p = u.prefs = u.prefs || {};
      if (rest === "digest") { p.digest = !p.digest; msg = `Digest ${p.digest ? "on" : "off"}`; }
      else if (rest === "hour") {
        const hours = [5, 13, 21];
        p.digest_hour_utc = hours[(hours.indexOf(p.digest_hour_utc || 21) + 1) % 3];
        msg = `Digest hour ${p.digest_hour_utc} UTC`;
      } else if (rest === "quiet") {
        const opts = ["off", "23-8"];
        p.quiet = opts[(opts.indexOf(p.quiet || "off") + 1) % 2];
        msg = `Quiet hours ${p.quiet}`;
      } else if (rest === "lang") {
        p.lang = p.lang === "en" ? "lv" : "en";
        msg = `Language ${p.lang}`;
      }
      return ud;
    });
    await answer(msg || "Saved");
    return send(chatId, `⚙️ ${msg}`, env);
  }

  if (action === "wh") {
    const state = await getState(env);
    const prod = findProduct(state_of(state), rest);
    if (!prod) return answer("Product not found.");
    await answer();
    return send(chatId, answerReadOnly("where", prod.id, state_of(state)), env);
  }

  // run-needing actions → dispatch with confirmed:true (CI skips re-confirmation)
  const confirmedMap = { ch: `/chart ${rest}`, ok: `/remove ${rest}`, chk: "/check",
                         add: null, th: null };
  if (action === "th") {
    const [slug, pct] = rest.split(":");
    confirmedMap.th = `/threshold ${slug} ${pct}`;
  }
  if (action === "add") {
    const [idx, key] = rest.split(":");
    confirmedMap.add = `/pick ${key} ${idx}`;
  }
  const text = confirmedMap[action];
  if (!text) return answer("Unknown action.");
  if (action === "chk" && member.role !== "owner") return answer("Owner only.");
  await answer("Queued a run — results in ~2–5 min.");
  const ok = await dispatch(env, text, chatId, true);
  if (!ok) await send(chatId, "⚠️ Could not trigger a run — check the Worker's GH_TOKEN.", env);
}

function splitCb(data) {
  const i = data.indexOf(":");
  return i < 0 ? [data, ""] : [data.slice(0, i), data.slice(i + 1)];
}

// ---------------------------------------------------------------------------
// users.json read-merge-write on the gist (small writes: watch/mute/settings)
// ---------------------------------------------------------------------------

async function updateUsers(env, fn) {
  let ud = { version: 1, users: [] };
  try {
    const r = await fetch(`https://api.github.com/gists/${env.GIST_ID}`, {
      headers: gistHeaders(env),
    });
    if (r.ok) {
      const gist = await r.json();
      const f = gist.files && gist.files["users.json"];
      if (f) {
        const text = f.truncated ? await (await fetch(f.raw_url)).text() : (f.content || "");
        try { ud = JSON.parse(text); } catch { /* keep default */ }
      }
    } else {
      console.error("users read failed:", r.status);
    }
  } catch (e) {
    console.error("users read error:", e);
  }
  if (!Array.isArray(ud.users)) ud.users = [];
  ud = fn(ud) || ud;
  try {
    const r = await fetch(`https://api.github.com/gists/${env.GIST_ID}`, {
      method: "PATCH",
      headers: gistHeaders(env),
      body: JSON.stringify({ files: { "users.json": { content: JSON.stringify(ud, null, 2) } } }),
    });
    if (!r.ok) console.error("users write failed:", r.status, (await r.text()).slice(0, 200));
    // bust the 60 s cache so read-only answers see the new membership right away
    await env.CACHE.delete("state");
  } catch (e) {
    console.error("users write error:", e);
  }
}

function gistHeaders(env) {
  return {
    "authorization": `Bearer ${env.GIST_TOKEN}`,
    "accept": "application/vnd.github+json",
    "user-agent": "cenu-mednieks-worker",
  };
}

// ---------------------------------------------------------------------------
// Read-only answers (plain text — nothing user-derived to escape except
// product names, which we escape anyway for safety).
// ---------------------------------------------------------------------------

function answerReadOnly(cmd, arg, { products, prices }) {
  const esc = escapeHtml;
  const prods = products.products || [];
  const fmt = (v) => (v == null ? "n/a" : `${Number(v).toFixed(2)} €`);
  const match = (part) => {
    part = (part || "").toLowerCase().trim();
    if (!part) return prods;
    return prods.filter((p) =>
      `${p.id} ${p.name}`.toLowerCase().includes(part) ||
      p.offers.some((o) => o.url.toLowerCase().includes(part)));
  };

  if (cmd === "start" || cmd === "help") {
    return [
      "🤖 Cenu Mednieks — price tracker",
      "",
      "Instant here: /list /prices /where /stats /best",
      "Run-triggered (~2–5 min): /add /add-url /threshold /remove /chart /check",
      "",
      "Alerts on drops, all-time lows, discounts and restocks — only for the",
      "products on your watchlist (/watch). Owner approves access requests.",
    ].join("\n");
  }

  if (cmd === "list") {
    if (!prods.length) return "Nothing tracked yet.";
    return "Tracked products:\n" + prods.map((p, i) =>
      `${i + 1}. ${esc(p.name || p.id)} — ${p.offers.length} store(s): ` +
      [...new Set(p.offers.map((o) => o.store))].sort().join(", ")).join("\n");
  }

  const bestOf = (p) => {
    let bo = null, bs = null;
    for (const o of p.offers || []) {
      const st = prices[o.url];
      const price = st && st.price;
      if (price != null && (bs == null || price < bs.price)) { bo = o; bs = st; }
    }
    return [bo, bs];
  };

  const atlSignal = (p, price) => {
    const atl = p.atl;
    if (!atl || atl.price == null || price == null || price <= 0) return "";
    if (price <= atl.price) return " · ATL";
    return ` · +${Math.round((price - atl.price) / atl.price * 100)}% above ATL`;
  };

  if (cmd === "prices") {
    const lines = ["💰 Best prices now"];
    for (const p of prods) {
      const [o, st] = bestOf(p);
      lines.push(o
        ? `• ${esc(p.name || p.id)}: ${fmt(st.price)} at ${esc(o.label || o.store)} ` +
          `${atlSignal(p, st.price)}(${p.offers.length} stores)`
        : `• ${esc(p.name || p.id)}: no data yet`);
    }
    return lines.join("\n");
  }

  if (cmd === "where") {
    const picks = match(arg).slice(0, 3);
    if (!picks.length) return `Nothing matches "${arg}".`;
    return picks.map((p) => {
      const lines = [`🏬 ${esc(p.name || p.id)} — ${p.offers.length} store(s)`];
      for (const o of p.offers || []) {
        const st = prices[o.url] || {};
        if (st.fails) lines.push(`• ${o.store}: ⚠ failing ×${st.fails}`);
        else if (st.price != null) lines.push(`• ${o.store}: ${fmt(st.price)}` +
          (st.shop ? ` (${esc(st.shop)})` : "") + (st.in_stock === false ? " ❌out" : ""));
        else lines.push(`• ${o.store}: not checked yet`);
      }
      return lines.join("\n");
    }).join("\n\n");
  }

  if (cmd === "stats" || cmd === "best") {
    const picks = cmd === "best" ? prods : match(arg).slice(0, 3);
    const scored = [];
    for (const p of picks) {
      const stats = productStats(p, prices);
      if (cmd === "stats") {
        const lines = [`📊 ${esc(p.name || p.id)} (${stats.stores} stores, ${stats.points} obs)`];
        if (stats.best.price != null)
          lines.push(`• Best now: ${fmt(stats.best.price)} at ${esc(stats.best.store || "?")}` +
            atlSignal(p, stats.best.price));
        if (stats.atl && stats.atl.price != null)
          lines.push(`• All-time low: ${fmt(stats.atl.price)} (${String(stats.atl.date).slice(0, 10)})`);
        if (stats.min7 != null) lines.push(`• 7-day min: ${fmt(stats.min7)} · 30-day min: ${fmt(stats.min30)}`);
        if (stats.avg30 != null) lines.push(`• 30-day avg: ${fmt(stats.avg30)}`);
        scored.push(lines.join("\n"));
      } else if (stats.best.price != null && stats.avg30 > 0) {
        scored.push({ depth: (stats.avg30 - stats.best.price) / stats.avg30 * 100, p, stats });
      }
    }
    if (cmd === "stats") return scored.join("\n\n") || "No data yet.";
    scored.sort((a, b) => b.depth - a.depth);
    if (!scored.length) return "Not enough history yet — try later.";
    return "🔥 Deepest current discounts (vs 30-day average)\n" +
      scored.slice(0, 5).map((s) => `• ${esc(s.p.name || s.p.id)}: ` +
        `${fmt(s.stats.best.price)} at ${esc(s.stats.best.store || "?")} ` +
        `(−${s.depth.toFixed(1)}% vs avg)`).join("\n");
  }

  return "Try /help";
}

function productStats(p, prices) {
  let best = null, bo = null;
  const pts = [];
  const now = Date.now() / 1000;
  for (const o of p.offers || []) {
    const st = prices[o.url] || {};
    if (st.price != null && (best == null || st.price < best)) {
      best = st.price;
      bo = o;
    }
    for (const h of st.history || []) {
      if (h.p == null) continue;
      const ts = Date.parse(h.t) / 1000;
      if (!Number.isNaN(ts)) pts.push([ts, h.p]);
    }
  }
  pts.sort((a, b) => a[0] - b[0]);
  const win = (seconds, op) => {
    const vals = pts.filter(([t]) => t >= now - seconds).map(([, v]) => v);
    return vals.length ? op(vals) : null;
  };
  return {
    best: { price: best, store: bo ? (bo.label || bo.store) : null },
    atl: p.atl || null,
    min7: win(7 * 86400, Math.min.bind(Math)),
    min30: win(30 * 86400, Math.min.bind(Math)),
    avg30: win(30 * 86400, (v) => Math.round(v.reduce((a, b) => a + b, 0) / v.length * 100) / 100),
    points: pts.length,
    stores: (p.offers || []).length,
  };
}

async function handleInline(iq, env) {
  const state = await getState(env);
  const results = [];
  const q = (iq.query || "").toLowerCase().trim();
  if (state) {
    for (const p of state.products.products || []) {
      if (q && !(p.name || "").toLowerCase().includes(q) && !p.id.includes(q)) continue;
      const stats = productStats(p, state.prices);
      if (results.length >= 12) break;
      const bp = stats.best.price;
      const title = bp != null
        ? `${p.name} — ${bp.toFixed(2)} € at ${stats.best.store}`
        : `${p.name} — no data yet`;
      const atl = p.atl && p.atl.price != null;
      const atlTxt = atl ? ` · ATL ${p.atl.price.toFixed(2)} €` : "";
      const text = `🛒 ${p.name}\n` + (bp != null
        ? `Best now: ${bp.toFixed(2)} € at ${stats.best.store}` +
          (atl && bp > p.atl.price
            ? ` (+${Math.round((bp - p.atl.price) / p.atl.price * 100)}% above ATL)` : "") +
          `${atlTxt}\n${stats.stores} stores tracked`
        : "Waiting for the next run…");
      results.push({
        type: "article",
        id: p.id,
        title,
        description: `${stats.stores} stores${atlTxt}`,
        input_message_content: { message_text: text },
      });
    }
  }
  await tg("answerInlineQuery", {
    inline_query_id: iq.id,
    results,
    cache_time: 30,
  }, env);
}

// ---------------------------------------------------------------------------
// Self-test (Q37 follow-up): /ping — end-to-end health report for the owner
// ---------------------------------------------------------------------------

async function selfTest(env) {
  const lines = ["🩺 Worker self-test"];
  try {
    const state = await getState(env);
    if (!state) {
      lines.push("• Gist fetch: ❌ (bad GIST_TOKEN or GIST_ID — wrangler secret put GIST_TOKEN)");
    } else {
      const nProds = (state.products.products || []).length;
      const nUsers = (state.users.users || []).length;
      lines.push(`• Gist fetch: ✅ (${nProds} products, ${nUsers} users)`);
    }
  } catch (e) {
    lines.push(`• Gist fetch: ❌ ${String(e).slice(0, 80)}`);
  }
  try {
    const r = await fetch(`https://api.github.com/repos/${env.REPO}`, {
      headers: {
        "authorization": `Bearer ${env.GH_TOKEN}`,
        "accept": "application/vnd.github+json",
        "user-agent": "cenu-mednieks-worker",
      },
    });
    lines.push(r.ok
      ? "• GH_TOKEN (dispatch): ✅"
      : `• GH_TOKEN (dispatch): ❌ HTTP ${r.status} — wrangler secret put GH_TOKEN`);
  } catch (e) {
    lines.push(`• GH_TOKEN: ❌ ${String(e).slice(0, 80)}`);
  }
  lines.push("• Telegram send: ✅ (you're reading this)");
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Telegram + state + dispatch plumbing
// ---------------------------------------------------------------------------

async function tg(method, payload, env) {
  const r = await fetch(`${API}/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) console.error(`telegram ${method} failed:`,
    r.status, (await r.text()).slice(0, 300));
  return r.ok;
}

function send(chatId, text, env) {
  return tg("sendMessage", { chat_id: chatId, text, disable_web_page_preview: true }, env);
}

async function dispatch(env, text, chatId, confirmed) {
  const r = await fetch(`https://api.github.com/repos/${env.REPO}/dispatches`, {
    method: "POST",
    headers: {
      "authorization": `Bearer ${env.GH_TOKEN}`,
      "accept": "application/vnd.github+json",
      "content-type": "application/json",
      "user-agent": "cenu-mednieks-worker",
    },
    body: JSON.stringify({
      event_type: "command-run",
      client_payload: { commands: [{ text, chat_id: chatId, confirmed: !!confirmed }] },
    }),
  });
  if (!r.ok) console.error("dispatch failed:", r.status, (await r.text()).slice(0, 300));
  return r.ok;
}

async function getState(env) {
  // 60 s KV cache; the state gist is the source of truth.
  const cached = await env.CACHE.get("state", "json");
  if (cached) return cached;
  const r = await fetch(`https://api.github.com/gists/${env.GIST_ID}`, {
    headers: gistHeaders(env),
  });
  if (!r.ok) {
    console.error("gist fetch failed:", r.status);
    return null;
  }
  const gist = await r.json();
  const readJson = async (name) => {
    const f = gist.files && gist.files[name];
    if (!f) return null;
    const text = f.truncated ? await (await fetch(f.raw_url)).text() : (f.content || "");
    try { return JSON.parse(text); } catch { return null; }
  };
  const [products, prices, users] = await Promise.all([
    readJson("products.json"), readJson("prices.json"), readJson("users.json"),
  ]);
  if (!products) return null;
  const state = { products, prices: prices || {}, users: users || { users: [] } };
  await env.CACHE.put("state", JSON.stringify(state), { expirationTtl: 60 });
  return state;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
