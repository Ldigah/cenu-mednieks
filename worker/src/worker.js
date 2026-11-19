/**
 * Cenu Mednieks — Telegram webhook + Mini App API (Cloudflare Worker, free tier).
 *
 * Telegram webhook: read-only commands answered instantly from a 60 s cached
 * copy of the state gist; mutating commands dispatch a full Actions run via
 * repository_dispatch (command text travels in client_payload.commands,
 * optionally confirmed:true when it came from a button). Callback taps
 * (approvals, watch/mute, settings, cf: confirmations) are handled directly.
 *
 * Mini App API (phase 2, Q9–Q15): POST /api/me|toggle|cmd with Telegram
 * initData — HMAC-verified against the bot token (Q10) — so the app gets a
 * per-user view and can toggle watch/mute (direct users.json writes) or queue
 * threshold/target/remove changes (confirmed dispatches). CORS is limited to
 * the Pages origin; the public data.json contains no user data (Q33).
 *
 * Membership lives in users.json (approved users only, Q1); OWNER_CHAT_ID is
 * the bootstrap fallback. Members get a 5-min run cooldown + 5 /search per
 * day (Q34); /check stays owner-only.
 */

const API = "https://api.telegram.org";
const READ_ONLY = new Set(["start", "help", "list", "prices", "where", "stats", "best", "tags"]);
// Instant users.json commands (phase-1/3 parity with the Python bot): no run needed
const INSTANT_USER = new Set(["watch", "unwatch", "mute", "unmute",
                              "settings", "quota", "whoami", "requests"]);
// Mutating chat commands (dispatch a run; CI enforces quota/permissions again)
const MUTATING = new Set(["add", "add_url", "threshold", "remove", "chart", "check", "pick",
                          "search", "target", "tag", "untag"]);
const COOLDOWN_S = 300;    // Q34: one run-triggering command per member per 5 min
const SEARCH_PER_DAY = 5;  // Q27
const GATES = ["github.io", "workers.dev", "localhost", "127.0.0.1"];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) return handleApi(request, env, url);
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

    if (READ_ONLY.has(cmd)) {
      const state = await getState(env);
      if (!state) return send(chatId, "⚠️ State gist unavailable — try again soon.", env);
      return send(chatId, answerReadOnly(cmd, arg, state), env);
    }

    if (cmd === "ping") {
      if (member.role !== "owner") return send(chatId, "⛔ Owner only.", env);
      return send(chatId, await selfTest(env), env);
    }

    if (INSTANT_USER.has(cmd)) {
      return handleInstantUser(env, member, cmd, arg, chatId);
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
// Instant users.json commands — parity with the Python bot (webhook mode never
// reaches CI's getUpdates, so these must be answerable here)
// ---------------------------------------------------------------------------

async function handleInstantUser(env, member, cmd, arg, chatId) {
  const state = state_of(await getState(env));
  const esc = escapeHtml;
  const fmt = (v) => (v == null ? "n/a" : `${Number(v).toFixed(2)} €`);

  if (cmd === "whoami") {
    const p = member.prefs || {};
    return send(chatId, [
      `👤 ${esc(member.name || String(member.chat_id))}` +
        (member.username ? ` (@${esc(member.username)})` : "") +
        ` — ${member.role}`,
      `• Watchlist: ${(member.watchlist || []).length} product(s)` +
        (member.watch_all ? " + everything" : ""),
      `• Muted: ${(member.muted || []).length}`,
      `• Quota: ${quotaLeft(member, state.products) ?? "unlimited"}`,
      `• Digest: ${p.digest ? "on" : "off"} at ${p.digest_hour_utc} UTC · ` +
        `quiet: ${p.quiet || "off"} · lang: ${p.lang || "en"}`,
    ].join("\n"), env);
  }

  if (cmd === "quota") {
    const q = quotaLeft(member, state.products);
    return send(chatId, q === null ? "♾ Unlimited (owner)."
      : `📦 You can add <b>${q}</b> more product(s).`, env);
  }

  if (cmd === "settings") {
    const p = member.prefs || {};
    return tg("sendMessage", {
      chat_id: chatId,
      text: `⚙️ Settings — ${esc(member.name || String(member.chat_id))}\n` +
        "Tap a row to change.\n" +
        `• Digest: ${p.digest ? "on" : "off"} at ${p.digest_hour_utc} UTC\n` +
        `• Quiet hours: ${p.quiet || "off"}\n` +
        `• Language: ${p.lang || "en"}`,
      reply_markup: { inline_keyboard: [
        [{ text: `📰 Digest: ${p.digest ? "on" : "off"}`, callback_data: "st:digest" }],
        [{ text: `🕐 Digest hour: ${p.digest_hour_utc} UTC`, callback_data: "st:hour" }],
        [{ text: `🌙 Quiet hours: ${p.quiet || "off"}`, callback_data: "st:quiet" }],
        [{ text: `🌐 Language: ${p.lang || "en"}`, callback_data: "st:lang" }],
      ] },
    }, env);
  }

  if (cmd === "requests") {
    if (member.role !== "owner") return send(chatId, "⛔ Owner only.", env);
    const pend = ((state.users && state.users.users) || [])
      .filter((u) => u.status === "pending");
    if (!pend.length) return send(chatId, "No pending access requests.", env);
    for (const u of pend) {
      await tg("sendMessage", {
        chat_id: chatId,
        text: `🙋 <b>${esc(u.name || String(u.chat_id))}</b>` +
          (u.username ? ` (@${esc(u.username)})` : "") + " requests access",
        reply_markup: { inline_keyboard: [[
          { text: "✅ Approve", callback_data: `apr:${u.chat_id}` },
          { text: "❌ Decline", callback_data: `dec:${u.chat_id}` },
        ]] },
      }, env);
    }
    return;
  }

  // watch / unwatch / mute / unmute
  const listName = (cmd === "watch" || cmd === "unwatch") ? "watchlist" : "muted";
  if (!arg.trim()) {
    if (cmd === "watch") {
      const names = (member.watchlist || [])
        .map((id) => productName(state, id)).filter(Boolean);
      const head = member.watch_all
        ? "⭐ You hear about everything (owner watch-all).\n"
        : "⭐ Your watchlist\n";
      return send(chatId, head + (names.map((n) => "• " + esc(n)).join("\n")
        || "Empty — /watch <name-part> or tap ⭐ on any alert card."), env);
    }
    return send(chatId, `Usage: /${cmd} <name-part>`, env);
  }

  const matches = findProductsAll(state, arg);
  if (!matches.length) return send(chatId, `Nothing matches "${arg}".`, env);
  if (matches.length > 1) {
    return send(chatId, "Multiple products match — be more specific:\n" +
      matches.slice(0, 10).map((p) => "• " + esc(p.name || p.id)).join("\n"), env);
  }
  const prod = matches[0];
  const adding = cmd === "watch" || cmd === "mute";
  const already = (member[listName] || []).includes(prod.id);
  if (adding === already) {
    return send(chatId, adding
      ? `Already ${cmd === "watch" ? "watching" : "muted"} ${esc(prod.name)}.`
      : `You weren't ${cmd === "watch" ? "watching" : "muted"} ${esc(prod.name)}.`, env);
  }
  let reply = "";
  await updateUsers(env, (ud) => {
    const u = (ud.users || []).find((x) => String(x.chat_id) === String(member.chat_id));
    if (!u) return ud;
    const list = u[listName] = u[listName] || [];
    const i = list.indexOf(prod.id);
    if (i >= 0) list.splice(i, 1);
    else list.push(prod.id);
    if (cmd === "watch" && adding) {  // watching clears a mute (Python parity)
      const m = u.muted || [];
      const j = m.indexOf(prod.id);
      if (j >= 0) m.splice(j, 1);
    }
    reply = adding
      ? (cmd === "watch" ? `⭐ Watching ${esc(prod.name)} — alerts will ping you here.`
                         : `🔕 Muted ${esc(prod.name)} — still watched, just silent.`)
      : (cmd === "unwatch" ? `💔 Unwatched ${esc(prod.name)}.`
                           : `🔔 Unmuted ${esc(prod.name)}.`);
    return ud;
  });
  return send(chatId, reply || "Saved.", env);
}

function productName(state, pid) {
  const p = (state.products.products || []).find((x) => x.id === pid);
  return p ? (p.name || p.id) : null;
}

function findProductsAll(state, arg) {
  const part = (arg || "").toLowerCase().trim().replace(/^#/, "");
  const prods = (state.products && state.products.products) || [];
  if (!part) return [];
  return prods.filter((p) => `${p.id} ${p.name}`.toLowerCase().includes(part)
    || (p.tags || []).some((x) => x === part)
    || (p.offers || []).some((o) => o.url.toLowerCase().includes(part)));
}

// ---------------------------------------------------------------------------
// Mini App API (Q9–Q15) — initData HMAC auth, no cookies, no user data leaks
// ---------------------------------------------------------------------------

function corsHeaders(request) {
  const origin = request.headers.get("origin") || "";
  let allow = null;
  if (!origin) allow = "*";                       // same-origin / curl
  else {
    try {
      const host = new URL(origin).hostname;
      if (GATES.some((g) => host === g || host.endsWith("." + g))) allow = origin;
    } catch { /* junk origin → no CORS */ }
  }
  return allow == null ? {} : {
    "access-control-allow-origin": allow,
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "content-type",
    "vary": "Origin",
  };
}

function jsonResp(obj, status, cors) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: Object.assign({ "content-type": "application/json" }, cors || {}),
  });
}

async function handleApi(request, env, url) {
  const cors = corsHeaders(request);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (request.method !== "POST") {
    return jsonResp({ ok: false, error: "POST only" }, 405, cors);
  }
  let body;
  try { body = await request.json(); } catch {
    return jsonResp({ ok: false, error: "bad json" }, 400, cors);
  }
  const identity = body && body.initData
    ? await verifyInitData(body.initData, env.TELEGRAM_BOT_TOKEN) : null;
  if (!identity) return jsonResp({ ok: false, error: "unauthorized" }, 401, cors);
  const member = await memberOf(env, identity.chatId);
  if (!member || member.status !== "active") {
    return jsonResp({ ok: false, error: "not an active member" }, 403, cors);
  }

  if (url.pathname === "/api/me") {
    const state = state_of(await getState(env));
    const user = Object.assign({}, member, { quota_left: quotaLeft(member, state.products) });
    return jsonResp({ ok: true, user }, 200, cors);
  }

  if (url.pathname === "/api/toggle") {
    const action = body.action === "mute" ? "mute" : "watch";
    const prod = findProduct(state_of(await getState(env)), body.pid || "");
    if (!prod) return jsonResp({ ok: false, error: "unknown product" }, 404, cors);
    let on = false;
    await updateUsers(env, (ud) => {
      const u = (ud.users || []).find((x) => String(x.chat_id) === String(member.chat_id));
      if (!u) return ud;
      const list = action === "watch" ? (u.watchlist = u.watchlist || [])
                                      : (u.muted = u.muted || []);
      const i = list.indexOf(prod.id);
      if (i >= 0) list.splice(i, 1);
      else list.push(prod.id);
      on = i < 0;
      if (on && action === "watch") {  // watching clears a mute (same as ⭐ in chat)
        const m = u.muted || [];
        const j = m.indexOf(prod.id);
        if (j >= 0) m.splice(j, 1);
      }
      return ud;
    });
    return jsonResp({ ok: true, on }, 200, cors);
  }

  if (url.pathname === "/api/cmd") {
    const state = state_of(await getState(env));
    const prod = findProduct(state, body.pid || "");
    if (!prod) return jsonResp({ ok: false, error: "unknown product" }, 404, cors);
    const mine = member.role === "owner" || prod.added_by == null
      || String(prod.added_by) === String(member.chat_id);
    if (!mine) return jsonResp({ ok: false, error: "not allowed" }, 403, cors);
    let text = null;
    if (body.cmd === "remove") text = `/remove ${prod.id}`;
    else if (body.cmd === "threshold") {
      const pct = Number(body.pct);
      if (!(pct >= 0.1 && pct <= 90)) return jsonResp({ ok: false, error: "bad pct" }, 400, cors);
      text = `/threshold ${prod.id} ${pct}`;
    } else if (body.cmd === "target") {
      const price = Number(body.price);
      if (!(price >= 0.5 && price <= 100000)) {
        return jsonResp({ ok: false, error: "bad price" }, 400, cors);
      }
      text = `/target ${prod.id} ${price}`;
    } else return jsonResp({ ok: false, error: "unknown cmd" }, 400, cors);
    if (member.role !== "owner") {
      const cd = await env.CACHE.get(`cd:${member.chat_id}`);
      if (cd) return jsonResp({ ok: false, error: "cooldown" }, 429, cors);
      await env.CACHE.put(`cd:${member.chat_id}`, String(Date.now()),
                          { expirationTtl: COOLDOWN_S });
    }
    const ok = await dispatch(env, text, identity.chatId, true);
    return jsonResp(ok ? { ok: true, queued: true }
                       : { ok: false, error: "dispatch failed" }, ok ? 200 : 502, cors);
  }
  return jsonResp({ ok: false, error: "not found" }, 404, cors);
}

/** Telegram Mini App initData validation: hex(HMAC(HMAC(bot, "WebAppData"), data)) */
async function verifyInitData(initData, botToken) {
  const parts = String(initData || "").split("&");
  const hashPart = parts.find((p) => p.startsWith("hash="));
  if (!hashPart || !botToken) return null;
  const hash = hashPart.slice(5);
  const dataCheck = parts.filter((p) => p !== hashPart).sort().join("\n");
  const enc = new TextEncoder();
  const keyBot = await crypto.subtle.importKey("raw", enc.encode(botToken),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const secret = await crypto.subtle.sign("HMAC", keyBot, enc.encode("WebAppData"));
  const key = await crypto.subtle.importKey("raw", secret,
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(dataCheck));
  const hex = [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
  if (hex !== hash) return null;
  const params = new URLSearchParams(initData);
  const authDate = Number(params.get("auth_date") || 0);
  if (!authDate || Date.now() / 1000 - authDate > 86400) return null;  // 24 h freshness
  let user = null;
  try { user = JSON.parse(params.get("user") || "null"); } catch { /* no user */ }
  if (!user || !user.id) return null;
  return { chatId: user.id, name: user.first_name || "", username: user.username || "" };
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
             watch_all: true, watchlist: [], muted: [], targets: {},
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
  if (member.role !== "owner" && cmd !== "search") {
    const cd = await env.CACHE.get(`cd:${member.chat_id}`);
    if (cd) return "⏱ Easy there — one run-triggering command per 5 minutes.";
    await env.CACHE.put(`cd:${member.chat_id}`, String(Date.now()), { expirationTtl: COOLDOWN_S });
  }
  if (cmd === "check") return member.role === "owner"
    ? null : "⛔ Owner only — /check triggers a full paid run.";
  if (cmd === "search") {
    if (member.role !== "owner") {
      const day = new Date().toISOString().slice(0, 10);
      const key = `sr:${member.chat_id}:${day}`;
      const n = Number(await env.CACHE.get(key) || 0);
      if (n >= SEARCH_PER_DAY) {
        return `🔎 /search limit reached today (${SEARCH_PER_DAY}/day) — /add is unlimited.`;
      }
      await env.CACHE.put(key, String(n + 1), { expirationTtl: 90000 });
    }
    if (!arg.trim()) return "Usage: /search <name>";
    return null;
  }
  if (cmd === "add" || cmd === "add_url") {
    const q = quotaLeft(member, state.products);
    if (q !== null && q <= 0) return "📦 Quota reached — ask the owner to raise it.";
    return null;
  }
  if (cmd === "remove" || cmd === "threshold" || cmd === "target"
      || cmd === "tag" || cmd === "untag") {
    const prod = findProduct(state, arg);
    if (prod) {
      const mine = member.role === "owner"
        || String(prod.added_by) === String(member.chat_id)
        || prod.added_by == null;
      if (!mine) return "⛔ Only the owner or whoever added this product can change it.";
    } else if (!arg.split(/\s+/)[0]) {
      return "Usage: /" + cmd + " <name-part> ...";
    }
    return null; // unknown name-parts are handled (with a real reply) by the CI run
  }
  return null;
}

function findProduct(state, arg) {
  const part = (arg || "").toLowerCase().trim().replace(/^#/, "");
  const prods = (state.products && state.products.products) || [];
  if (!part) return null;
  return prods.find((p) => `${p.id} ${p.name}`.toLowerCase().includes(part)
    || (p.tags || []).some((x) => x === part)
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
      if (u && u.status === "pending") {
        u.status = action === "apr" ? "active" : "blocked";
        ok = true;
      }
      return ud;
    });
    if (!ok) return answer("Request is gone.");
    await answer(action === "apr" ? "Approved!" : "Declined.");
    await send(chatId, action === "apr" ? `✅ Approved user ${target}.`
                                        : `❌ Declined user ${target}.`, env);
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

  if (action === "cf") {
    // short-key confirmation (phase 2): dispatch the stored command as confirmed
    const state = await getState(env);
    const op = ((state.bot_state || {}).pending_ops || {})[rest];
    if (!op) return answer("Expired — run the command again.");
    if (op.chat_id != null && String(op.chat_id) !== String(chatId)
        && member.role !== "owner") return answer("Not yours to confirm.");
    await answer("Queued a run — results in ~2–5 min.");
    const ok = await dispatch(env, op.cmd, chatId, true);
    if (!ok) {
      await send(chatId, "⚠️ Could not trigger a run — check the Worker's GH_TOKEN.", env);
    }
    return;
  }

  // run-needing actions → dispatch with confirmed:true (CI skips re-confirmation)
  const confirmedMap = { ch: `/chart ${rest}`, ok: `/remove ${rest}`, chk: "/check",
                         add: null, th: null };
  if (action === "th") {  // legacy phase-1 cards still floating in chats
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
    part = (part || "").toLowerCase().trim().replace(/^#/, "");
    if (!part) return prods;
    return prods.filter((p) =>
      `${p.id} ${p.name}`.toLowerCase().includes(part) ||
      (p.tags || []).some((x) => x === part) ||
      p.offers.some((o) => o.url.toLowerCase().includes(part)));
  };

  if (cmd === "start" || cmd === "help") {
    return [
      "🤖 Cenu Mednieks — price tracker",
      "",
      "Instant here: /list /prices /where /stats /best /tags",
      "/watch /unwatch /mute /unmute /settings /quota /whoami /requests",
      "Run-triggered (~2–5 min): /add /add_url /threshold /target /remove",
      "/chart /check /search",
      "",
      "Alerts on drops, all-time lows, discounts, restocks and target hits —",
      "only for the products on your watchlist (/watch). Owner approves access.",
    ].join("\n");
  }

  if (cmd === "tags") {
    const idx = {};
    for (const p of prods) for (const t of p.tags || []) idx[t] = (idx[t] || 0) + 1;
    const keys = Object.keys(idx).sort();
    if (!keys.length) return "No tags yet — /tag <name-part> #tag to add one.";
    return "🏷 Tags:\n" + keys.map((t) => `#${t} ×${idx[t]}`).join("\n");
  }

  if (cmd === "list") {
    const shown = arg.startsWith("#")
      ? prods.filter((p) => (p.tags || []).includes(arg.slice(1).toLowerCase()))
      : prods;
    if (!shown.length) return arg.startsWith("#")
      ? `Nothing tagged ${arg}.` : "Nothing tracked yet.";
    return "Tracked products:\n" + shown.map((p, i) =>
      `${i + 1}. ${esc(p.name || p.id)} — ${p.offers.length} store(s): ` +
      [...new Set(p.offers.map((o) => o.store))].sort().join(", ") +
      ((p.tags || []).length ? "  " + p.tags.map((t) => "#" + t).join(" ") : "")).join("\n");
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
        if (stats.best.price != null) {
          lines.push(`• Best now: ${fmt(stats.best.price)} at ${esc(stats.best.store || "?")}` +
            atlSignal(p, stats.best.price));
        }
        if (stats.atl && stats.atl.price != null) {
          lines.push(`• All-time low: ${fmt(stats.atl.price)} (${String(stats.atl.date).slice(0, 10)})`);
        }
        if (stats.min7 != null) lines.push(`• 7-day min: ${fmt(stats.min7)} · 30-day min: ${fmt(stats.min30)}`);
        if (stats.avg30 != null) lines.push(`• 30-day avg: ${fmt(stats.avg30)}`);
        const sig = [];
        if (stats.vol != null) sig.push(`volatility ±${stats.vol}%`);
        if (stats.drops30 != null) sig.push(`${stats.drops30} drops ≥5%/30d`);
        if (sig.length) lines.push("• " + sig.join(" · "));
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
  // volatility: stdev % of mean over 30d (mirror of catalog.volatility)
  let vol = null;
  {
    const vals = pts.filter(([t]) => t >= now - 30 * 86400).map(([, v]) => v);
    if (vals.length >= 6) {
      const m = vals.reduce((a, b) => a + b, 0) / vals.length;
      if (m > 0) {
        const varr = vals.reduce((a, b) => a + (b - m) * (b - m), 0) / vals.length;
        vol = Math.round(Math.sqrt(varr) / m * 1000) / 10;
      }
    }
  }
  return {
    best: { price: best, store: bo ? (bo.label || bo.store) : null },
    atl: p.atl || null,
    min7: win(7 * 86400, Math.min.bind(Math)),
    min30: win(30 * 86400, Math.min.bind(Math)),
    avg30: win(30 * 86400, (v) => Math.round(v.reduce((a, b) => a + b, 0) / v.length * 100) / 100),
    vol,
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
      if (q && !(p.name || "").toLowerCase().includes(q) && !p.id.includes(q)
          && !(p.tags || []).some((x) => x.includes(q))) continue;
      const stats = productStats(p, state.prices);
      if (results.length >= 12) break;
      const bp = stats.best.price;
      const title = bp != null
        ? `${p.name} — ${bp.toFixed(2)} € at ${stats.best.store}`
        : `${p.name} — no data yet`;
      const atl = p.atl && p.atl.price != null;
      const atlTxt = atl ? ` · ATL ${p.atl.price.toFixed(2)} €` : "";
      const above = atl && bp != null && bp > p.atl.price
        ? ` (+${Math.round((bp - p.atl.price) / p.atl.price * 100)}% above ATL)` : "";
      const text = `🛒 ${p.name}\n` + (bp != null
        ? `Best now: ${bp.toFixed(2)} € at ${stats.best.store}${above}${atlTxt}\n` +
          `${stats.stores} stores tracked · open the app for charts & watch buttons`
        : "Waiting for the next run…");
      const pages = env.PAGES_URL || "https://ldigah.github.io/cenu-mednieks/";
      results.push({
        type: "article",
        id: p.id,
        title,
        description: `${stats.stores} stores${atlTxt}`,
        input_message_content: { message_text: text },
        reply_markup: { inline_keyboard: [[
          { text: "🌐 App", url: `${pages}?p=${p.id}` },
          { text: "⭐ Watch", callback_data: `wt:${p.id}` },
        ]] },
        thumb_url: p.image || undefined,
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
  const [products, prices, users, botState] = await Promise.all([
    readJson("products.json"), readJson("prices.json"),
    readJson("users.json"), readJson("bot_state.json"),
  ]);
  if (!products) return null;
  const state = { products, prices: prices || {}, users: users || { users: [] },
                  bot_state: botState || {} };
  await env.CACHE.put("state", JSON.stringify(state), { expirationTtl: 60 });
  return state;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
