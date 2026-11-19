/**
 * Cenu Mednieks — Telegram webhook receiver (Cloudflare Worker, free tier).
 *
 * Read-only commands (/list /prices /where /stats /best /help) are answered in
 * ~1 s from a 60-second-cached copy of the state gist. Mutating commands
 * (/add /add-url /threshold /remove /chart /check) trigger a full GitHub
 * Actions run via repository_dispatch — the run then polls Telegram getUpdates
 * and processes them with the real Python logic (offset stays consistent).
 * Inline queries (@bot <name>) get live price cards in any chat.
 */

const API = "https://api.telegram.org";
const READ_ONLY = new Set(["start", "help", "list", "prices", "where", "stats", "best"]);

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
    const msg = update.message;
    if (!msg || !msg.text || !msg.text.startsWith("/")) return;
    const [rawCmd, ...rest] = msg.text.slice(1).split(" ");
    const cmd = rawCmd.split("@")[0].toLowerCase();
    const arg = rest.join(" ").trim();
    const chatId = msg.chat && msg.chat.id;
    if (!chatId) return;

    if (READ_ONLY.has(cmd)) {
      const state = await getState(env);
      if (!state) return send(chatId, "⚠️ State gist unavailable — try again soon.", env);
      const text = answerReadOnly(cmd, arg, state);
      return send(chatId, text, env);
    }
    if (["add", "add-url", "threshold", "remove", "chart", "check"].includes(cmd)) {
      const ok = await dispatch(env);
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
// Read-only answers (plain text — no parse_mode, nothing user-derived to escape
// except product names, which we escape anyway for safety).
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
      "Scheduled runs: 3×/day. Alerts on best-price drops, all-time lows,",
      "discounts and restocks.",
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

  if (cmd === "prices") {
    const lines = ["💰 Best prices now"];
    for (const p of prods) {
      const [o, st] = bestOf(p);
      lines.push(o
        ? `• ${esc(p.name || p.id)}: ${fmt(st.price)} at ${esc(o.label || o.store)} ` +
          `(${p.offers.length} stores)`
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
          lines.push(`• Best now: ${fmt(stats.best.price)} at ${esc(stats.best.store || "?")}`);
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
      const atlTxt = stats.atl && stats.atl.price != null
        ? ` · ATL ${stats.atl.price.toFixed(2)} €` : "";
      const text = `🛒 ${p.name}\n` + (bp != null
        ? `Best now: ${bp.toFixed(2)} € at ${stats.best.store}${atlTxt}\n${stats.stores} stores tracked`
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
// Telegram + state + dispatch plumbing
// ---------------------------------------------------------------------------

async function tg(method, payload, env) {
  const r = await fetch(`${API}/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) console.error(`telegram ${method} failed:`, (await r.text()).slice(0, 300));
  return r.ok;
}

function send(chatId, text, env) {
  return tg("sendMessage", { chat_id: chatId, text, disable_web_page_preview: true }, env);
}

async function dispatch(env) {
  const r = await fetch(`https://api.github.com/repos/${env.REPO}/dispatches`, {
    method: "POST",
    headers: {
      "authorization": `Bearer ${env.GH_TOKEN}`,
      "accept": "application/vnd.github+json",
      "content-type": "application/json",
      "user-agent": "cenu-mednieks-worker",
    },
    body: JSON.stringify({ event_type: "command-run" }),
  });
  if (!r.ok) console.error("dispatch failed:", r.status, (await r.text()).slice(0, 300));
  return r.ok;
}

async function getState(env) {
  // 60 s KV cache; the state gist is the source of truth.
  const cached = await env.CACHE.get("state", "json");
  if (cached) return cached;
  const r = await fetch(`https://api.github.com/gists/${env.GIST_ID}`, {
    headers: {
      "authorization": `Bearer ${env.GIST_TOKEN}`,
      "accept": "application/vnd.github+json",
      "user-agent": "cenu-mednieks-worker",
    },
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
  const [products, prices] = await Promise.all([
    readJson("products.json"), readJson("prices.json"),
  ]);
  if (!products) return null;
  const state = { products, prices: prices || {} };
  await env.CACHE.put("state", JSON.stringify(state), { expirationTtl: 60 });
  return state;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
