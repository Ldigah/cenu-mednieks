/* Cenu Mednieks Mini App v2 — light vanilla JS (Q11), Telegram theme (Q12).
 * Public data comes from data.json (no user data — Q33); personal state
 * (watchlist, muted, targets, prefs) comes from the Worker /api/* endpoints,
 * authenticated by Telegram initData HMAC (Q10).
 */
"use strict";

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
const $app = document.getElementById("app");
const $toast = document.getElementById("toast");

const S = { data: null, me: null, pid: null, tag: "", query: "", lang: "en" };

const L = {
  en: { deals: "🔥 Today's deals", all: "All products", search: "Search…",
        back: "← Back", buy: "Buy at", stores: "Stores", chart: "90-day best price",
        best: "Best now", atl: "All-time low", above: "above ATL", vol: "Volatility",
        drops: "Drops ≥5%/30d", dow: "Cheapest day", target: "Your target", avg30: "30-day avg",
        watch: "⭐ Watch", watching: "⭐ Watching", mute: "🔕 Mute", muted: "🔕 Muted",
        share: "Share", remove: "🗑 Remove", targetSet: "🎯 Set target",
        unmuted: "🔔 Unmuted",
        threshold: "🎚 Threshold %", save: "Save", out: "out of stock", fail: "failing",
        notChecked: "not checked yet", openChat: "Track in chat", offline: "offline",
        days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        removed: "Removed", queued: "Queued a run — ~2–5 min", saved: "Saved",
        memberOnly: "Ask the owner for access to use actions" },
  lv: { deals: "🔥 Šodienas atlaides", all: "Visi produkti", search: "Meklēt…",
        back: "← Atpakaļ", buy: "Pirkt", stores: "Veikali", chart: "90 dienu labākā cena",
        best: "Labākā cena", atl: "Zemākā cena", above: "virs minimuma", vol: "Nestabilitāte",
        drops: "Kritumi ≥5%/30d", dow: "Lētākā diena", target: "Tavs mērķis", avg30: "30d vidējā",
        watch: "⭐ Sekot", watching: "⭐ Sekoju", mute: "🔕 Apklusināt", muted: "🔕 Apklusināts",
        share: "Dalīties", remove: "🗑 Noņemt", targetSet: "🎯 Uzstādīt mērķi",
        unmuted: "🔔 Ar paziņojumiem",
        threshold: "🔒 Slieksnis %", save: "Saglabāt", out: "nav noliktavā", fail: "neizdodas",
        notChecked: "vēl nav pārbaudīts", openChat: "Pievienot čatā", offline: "bez saites",
        days: ["Pr", "Ot", "Tr", "Ce", "Pk", "Se", "Sv"],
        removed: "Noņemts", queued: "Rinda — ~2–5 min", saved: "Saglabāts",
        memberOnly: "Palūdz īpašniekam piešķirt piekļuvi" },
};
const t = (k) => (L[S.lang] && L[S.lang][k]) || L.en[k] || k;

function fmt(v) { return v == null ? "—" : Number(v).toFixed(2) + " €"; }
function esc(s) { return String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

function toast(msg) {
  $toast.textContent = msg; $toast.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { $toast.hidden = true; }, 2600);
}

async function api(path, body) {
  const r = await fetch((S.data.api_base || "") + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}
async function callApi(path, body) {
  try { return await api(path, body); }
  catch (e) { toast("⚠️ " + e.message); return null; }
}
function authBody(extra) {
  const base = tg && tg.initData ? { initData: tg.initData } : null;
  if (!base) return null;
  return Object.assign(base, extra || {});
}

/* ---------- theme (Q12) ---------- */
function applyTheme() {
  if (!tg || !tg.themeParams) return;
  const p = tg.themeParams, r = document.documentElement.style;
  const set = (k, v, fb) => r.setProperty(k, v || fb);
  set("--bg", p.bg_color, "#ffffff"); set("--card", p.secondary_bg_color, "#f4f4f5");
  set("--border", p.hint_color && p.secondary_bg_color ? p.hint_color + "33" : "#e4e4e7",
      "#e4e4e7");
  set("--text", p.text_color, "#111827"); set("--muted", p.hint_color, "#6b7280");
  set("--accent", p.button_color, "#2f81f7");
  set("--good", "#16a34a"); set("--warn", p.destructive_message_color || "#dc2626");
}
if (tg) {
  tg.ready(); tg.expand();
  applyTheme();
  if (tg.onEvent) tg.onEvent("themeChanged", applyTheme);
}

/* ---------- data loading ---------- */
function productById(pid) {
  return (S.data.products || []).find((p) => p.id === pid) || null;
}
function aboveAtl(p) {
  const best = p.stats && p.stats.best && p.stats.best.price;
  const atl = p.atl && p.atl.price;
  if (best == null || !atl) return null;
  return best <= atl ? 0 : Math.round((best - atl) / atl * 100);
}
function dropVsAvg(p) {
  const avg = p.stats && p.stats.avg30;
  const best = p.stats && p.stats.best && p.stats.best.price;
  if (!avg || best == null) return null;
  const d = (avg - best) / avg * 100;
  return d > 1 ? d : null;
}
function isWatching(p) {
  if (!S.me) return false;
  return Boolean(S.me.watch_all) || (S.me.watchlist || []).includes(p.id);
}
function isMuted(p) { return Boolean(S.me) && (S.me.muted || []).includes(p.id); }

async function load() {
  const r = await fetch("data.json", { cache: "no-store" });
  S.data = await r.json();
  if (tg && tg.initData) {
    const me = await callApi("/api/me", { initData: tg.initData });
    if (me && me.ok) { S.me = me.user; S.lang = (me.user.prefs || {}).lang || "en"; }
  }
  const tagline = document.getElementById("tagline");
  if (tagline) tagline.textContent = t("all") + " · " + (S.data.generated || "").slice(0, 10);
  const start = tg && tg.initDataUnsafe ? tg.initDataUnsafe.start_param : null;
  const url = new URL(location.href);
  const wanted = start || url.searchParams.get("p");
  if (wanted && productById(wanted)) { S.pid = wanted; renderProduct(); }
  else renderList();
  renderUserbox();
}

function renderUserbox() {
  const box = document.getElementById("userbox");
  if (!box) return;
  if (S.me) {
    const q = S.me.quota_left;
    box.innerHTML = esc(S.me.name || "you") + (S.me.watch_all ? " · 👑" : "") +
      (q == null ? "" : ` · 📦 ${q}`);
  } else if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
    box.textContent = (tg.initDataUnsafe.user.first_name || "") + " · " + t("memberOnly");
  } else {
    box.textContent = "public view";
  }
}

/* ---------- list view (Q25 deals feed on top) ---------- */
function visibleProducts() {
  let items = S.data.products || [];
  if (S.tag) items = items.filter((p) => (p.tags || []).includes(S.tag));
  if (S.query) {
    const q = S.query.toLowerCase();
    items = items.filter((p) => (p.name || "").toLowerCase().includes(q)
      || p.id.includes(q) || (p.tags || []).some((x) => x.includes(q)));
  }
  return items;
}

function productCard(p) {
  const best = p.stats && p.stats.best;
  const atlPct = aboveAtl(p);
  const deal = dropVsAvg(p);
  const badge = atlPct != null && atlPct <= 0
    ? '<span class="badge atl">🏆 ATL</span>'
    : (deal ? `<span class="badge hot">🔥 −${deal.toFixed(0)}%</span>` : "")
      + (atlPct != null ? `<span class="badge">+${atlPct}% ${t("above")}</span>` : "");
  const target = S.me && S.me.targets && S.me.targets[p.id] != null;
  const hit = target && best && best.price != null && best.price <= S.me.targets[p.id];
  const img = p.image ? `<img class="thumb" src="${esc(p.image)}" alt="" loading="lazy">`
    : '<img class="thumb" alt="">';
  return `<div class="card" data-pid="${esc(p.id)}">
    <div class="top">${img}
      <div style="flex:1">
        <div class="name">${esc(p.name || p.id)}${target && !hit
          ? '<span class="badge">🎯</span>' : ""}${hit
            ? '<span class="badge hit">🎯 hit</span>' : ""}</div>
        <div class="meta">${(p.offers || []).length} ${t("stores").toLowerCase()}</div>
        <div><span class="price">${best && best.price != null ? fmt(best.price) : "—"}</span>
          <span class="meta">${best && best.store ? "· " + esc(best.store) : ""}</span>
          ${badge}</div>
      </div>
    </div>
  </div>`;
}

function renderList() {
  S.pid = null;
  const items = visibleProducts();
  const tags = S.data.tags || {};
  const chips = Object.keys(tags).map((tag) =>
    `<span class="chip ${S.tag === tag ? "on" : ""}" data-tag="${esc(tag)}">#${esc(tag)}</span>`
  ).join("");
  const deals = (S.data.products || [])
    .map((p) => [dropVsAvg(p), p]).filter(([d]) => d)
    .sort((a, b) => b[0] - a[0]).slice(0, 5);
  $app.innerHTML = `
    <input class="searchbox" id="q" placeholder="${esc(t("search"))}" value="${esc(S.query)}">
    ${chips ? `<div class="chips">${S.tag
      ? `<span class="chip ${S.tag ? "" : "on"}" data-tag="">✕</span>` : ""}${chips}</div>` : ""}
    ${deals.length ? `<div class="section">${t("deals")}</div>
      ${deals.map(([d, p]) => productCard(p)).join("")}` : ""}
    <div class="section">${t("all")}${items.length ? ` (${items.length})` : ""}</div>
    ${items.map(productCard).join("") || `<div class="loading">${esc(t("all"))}: 0</div>`}
    <footer>refreshed ${esc((S.data.generated || "").slice(0, 16))} ·
      <a href="https://github.com/Ldigah/cenu-mednieks">GitHub</a></footer>`;
  const q = document.getElementById("q");
  q.addEventListener("input", () => { S.query = q.value.trim();
    const el = document.getElementById("q"); /* re-render keeps focus via value */ });
  q.addEventListener("change", () => { S.query = q.value.trim(); renderList(); });
  $app.querySelectorAll("[data-tag]").forEach((c) =>
    c.addEventListener("click", () => { S.tag = c.dataset.tag === "" ? "" : c.dataset.tag;
      renderList(); }));
  $app.querySelectorAll("[data-pid]").forEach((c) =>
    c.addEventListener("click", () => { S.pid = c.dataset.pid; renderProduct(); }));
}

/* ---------- product detail (Q14) ---------- */
function statCell(label, value) {
  return `<div class="cell"><span>${esc(label)}</span><b>${value}</b></div>`;
}

function renderProduct() {
  const p = productById(S.pid);
  if (!p) { renderList(); return; }
  const best = (p.stats && p.stats.best) || {};
  const atlPct = aboveAtl(p);
  const st = p.stats || {};
  const signals = [];
  if (st.vol != null) signals.push(statCell(t("vol"), `±${Number(st.vol).toFixed(1)}%`));
  if (st.drops30 != null) signals.push(statCell(t("drops"), String(st.drops30)));
  if (st.dow) signals.push(statCell(t("dow"),
    `${(L[S.lang] || L.en).days[st.dow.day]} · ${fmt(st.dow.avg)}`));
  if (st.avg30 != null) signals.push(statCell(t("avg30"), fmt(st.avg30)));
  const myTarget = S.me && S.me.targets ? S.me.targets[p.id] : null;
  const storeRows = (p.offers || []).map((o) => {
    const s = o.price != null
      ? `<b>${fmt(o.price)}</b>${o.old_price && o.price && o.old_price > o.price
          ? ` <span class="meta"><s>${fmt(o.old_price)}</s></s></span>` : ""}`
      : (o.fails ? `<span style="color:var(--warn)">${esc(t("fail"))} ×${o.fails}</span>`
                 : `<span class="meta">${esc(t("notChecked"))}</span>`);
    const stock = o.in_stock === false ? ` <span class="badge off">${esc(t("out"))}</span>` : "";
    return `<tr><td><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.label
      || o.store)}</a></td><td>${s}${stock}</td></tr>`;
  }).join("");
  const watchBtn = S.me
    ? `<button class="btn ${isWatching(p) ? "on" : ""}" id="b-watch">${isWatching(p)
        ? t("watching") : t("watch")}</button>
       <button class="btn ${isMuted(p) ? "on" : ""}" id="b-mute">${isMuted(p)
        ? t("muted") : t("mute")}</button>`
    : "";
  const ownerBtns = S.me
    ? `<button class="btn" id="b-threshold">${t("threshold")}</button>
       <button class="btn danger" id="b-remove">${t("remove")}</button>`
    : "";
  $app.innerHTML = `
    <span class="backlink" id="back">${esc(t("back"))}</span>
    <div class="card detail"><div class="top">
      ${p.image ? `<img class="big" src="${esc(p.image)}" alt="">` : ""}
      <div style="flex:1">
        <div class="name" style="font-size:1.05rem">${esc(p.name || p.id)}
          ${(p.tags || []).map((x) => `<span class="badge">#${esc(x)}</span>`).join("")}</div>
        <div><span class="price">${best.price != null ? fmt(best.price) : "—"}</span>
          ${atlPct != null && atlPct > 0
            ? `<span class="badge">+${atlPct}% ${t("above")}</span>` : ""}
          ${atlPct === 0 ? '<span class="badge atl">🏆 ATL</span>' : ""}</div>
        ${best.url ? `<a class="buybtn" href="${esc(best.url)}" target="_blank"
          rel="noopener">${esc(t("buy"))} · ${esc(best.store || "")}</a>` : ""}
      </div>
    </div></div>
    ${p.chart ? `<img class="chart" src="${esc(p.chart)}" alt="${esc(t("chart"))}">` : ""}
    <div class="grid">
      ${statCell(t("atl"), p.atl && p.atl.price != null ? fmt(p.atl.price) : "—")}
      ${signals.join("")}
      ${myTarget != null ? statCell(t("target"), fmt(myTarget) +
        (best.price != null && best.price <= myTarget ? " 🎯" : "")) : ""}
    </div>
    <div class="section">${t("stores")} (${(p.offers || []).length})</div>
    <table>${storeRows}</table>
    <div class="actions">${watchBtn}
      <button class="btn" id="b-share">${t("share")}</button>
      ${S.me ? `<button class="btn" id="b-target">${t("targetSet")}</button>` : ""}
      ${ownerBtns}</div>
    <div id="editor"></div>`;
  document.getElementById("back").addEventListener("click", renderList);
  const wire = (id, fn) => { const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn); };
  if (S.me) {
    wire("b-watch", () => toggle("watch", p));
    wire("b-mute", () => toggle("mute", p));
    wire("b-target", () => editValue("target", p,
      S.me.targets && S.me.targets[p.id] != null ? S.me.targets[p.id] : ""));
    wire("b-threshold", () => editValue("threshold", p, p.threshold_pct || 5));
    wire("b-remove", async () => {
      const sure = tg && tg.showConfirm ? await new Promise((res) => tg.showConfirm(
        `${t("remove")} ${p.name}?`, res)) : window.confirm(`${t("remove")} ${p.name}?`);
      if (!sure) return;
      const res = await callApi("/api/cmd", authBody({ cmd: "remove", pid: p.id }));
      if (res && res.ok) { toast(t("removed") + " · " + t("queued")); renderList(); }
    });
  }
  wire("b-share", async () => {
    const link = `${location.origin + location.pathname}?p=${encodeURIComponent(p.id)}`;
    try { await navigator.clipboard.writeText(link); toast("🔗 " + link.slice(0, 60) + "…"); }
    catch { toast(link); }
  });
}

async function toggle(action, p) {
  if (!S.me) return;
  const res = await callApi("/api/toggle", authBody({ action, pid: p.id }));
  if (res && res.ok) {
    if (action === "watch") {
      const on = res.on;
      if (on) { S.me.watchlist = S.me.watchlist || []; S.me.watchlist.push(p.id); }
      else S.me.watchlist = (S.me.watchlist || []).filter((x) => x !== p.id);
      toast(on ? t("watching") : t("watch"));
    } else {
      const on = res.on;
      if (on) { S.me.muted = S.me.muted || []; S.me.muted.push(p.id); }
      else S.me.muted = (S.me.muted || []).filter((x) => x !== p.id);
      toast(on ? t("muted") : t("unmuted"));
    }
    renderProduct();
  }
}

function editValue(kind, p, current) {
  const box = document.getElementById("editor");
  const label = kind === "target" ? t("targetSet") + " (€)" : t("threshold") + " (0.1–90)";
  box.innerHTML = `<div class="inline-edit">
    <input id="edit-in" inputmode="decimal" placeholder="${esc(label)}" value="${esc(current)}">
    <button class="btn primary" id="edit-save">${t("save")}</button></div>`;
  const input = document.getElementById("edit-in");
  input.focus();
  document.getElementById("edit-save").addEventListener("click", async () => {
    const raw = input.value.trim();
    const num = Number(raw.replace(",", "."));
    if (raw === "" || Number.isNaN(num)) { box.innerHTML = ""; return; }
    const res = await callApi("/api/cmd", authBody({ cmd: kind, pid: p.id,
      pct: kind === "threshold" ? num : undefined, price: kind === "target" ? num : undefined }));
    if (res && res.ok) {
      toast(t("queued"));
      if (kind === "target") { S.me.targets = S.me.targets || {}; S.me.targets[p.id] = num; }
      box.innerHTML = "";
      renderProduct();
    }
  });
}

load().catch((e) => { $app.innerHTML = `<div class="loading">⚠️ ${esc(e.message)}</div>`; });
