/* WizzOS gränssnitt – vanlig JavaScript, inga ramverk och inget byggsteg.
   Varje sida är en funktion i `pages` som ritar upp #view. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: { "x-wizzos": "1" }, credentials: "same-origin" };
  if (opts.body !== undefined) { o.headers["content-type"] = "application/json"; o.body = JSON.stringify(opts.body); }
  const r = await fetch(path, o);
  if (r.status === 401 && !path.startsWith("/api/login")) { showAuth(false); throw new Error("Inte inloggad"); }
  const type = r.headers.get("content-type") || "";
  const data = type.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || `Fel ${r.status}`);
  return data;
}

function toast(text, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`; t.textContent = text;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4500);
}

const fmtBytes = (b) => { const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < 4) { b /= 1024; i++; } return `${b.toFixed(i > 2 ? 1 : 0)} ${u[i]}`; };
const fmtUptime = (s) => { const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`; };
const greeting = () => { const h = new Date().getHours(); return t(h < 5 ? "God natt" : h < 10 ? "God morgon" : h < 17 ? "Hej" : h < 22 ? "God kväll" : "God natt"); };
const STATUS = { running: "Igång", partial: "Delvis igång", stopped: "Stoppad", restarting: "Startar om", exited: "Stoppad", created: "Skapad", paused: "Pausad" };

function icon(url, title, cls = "ico") {
  const u = /^\/static\/[\w.-]+$/.test(url || "") ? url : safeUrl(url);
  const letter = esc((title || "?").trim().charAt(0).toUpperCase());
  if (!u) return `<div class="ico-fallback">${letter}</div>`;
  return `<img class="${cls}" src="${esc(u)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-l="${letter}"
    onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'ico-fallback',textContent:this.dataset.l}))">`;
}

/* ---------------- Dialogruta, meny ---------------- */

function openModal(html, onClose) {
  $("#modal-body").innerHTML = html;
  $("#modal").classList.remove("hidden");
  $("#modal")._onClose = onClose;
  return $("#modal-body");
}
function closeModal() {
  const m = $("#modal");
  if (m.classList.contains("hidden")) return;
  m.classList.add("hidden");
  $("#modal-body").innerHTML = "";
  if (m._onClose) { const f = m._onClose; m._onClose = null; f(); }
}
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.closest("[data-close]")) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeMenu(); closeModal(); } });

function confirmBox(title, text, okText = "OK", danger = false, extra = "") {
  return new Promise((resolve) => {
    let done = false;
    const body = openModal(`<h3>${esc(title)}</h3><p>${esc(text)}</p>${extra}
      <div class="actions"><button class="btn" data-close>Avbryt</button>
      <button class="btn ${danger ? "danger" : "primary"}" id="ok">${esc(okText)}</button></div>`, () => { if (!done) resolve(null); });
    $("#ok", body).onclick = () => { done = true; const r = { ok: true, body }; const res = { ...r, checked: $("#extra-check", body)?.checked }; closeModal(); resolve(res); };
  });
}

function openMenu(anchor, items) {
  closeMenu();
  const m = document.createElement("div");
  m.className = "menu"; m.id = "menu";
  m.innerHTML = items.map((it, i) => it === "-" ? "<hr>" : `<button data-i="${i}" class="${it.danger ? "danger" : ""}">${esc(it.label)}</button>`).join("");
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  const left = Math.min(r.right - m.offsetWidth, innerWidth - m.offsetWidth - 8);
  let top = r.bottom + 6; if (top + m.offsetHeight > innerHeight - 8) top = r.top - m.offsetHeight - 6;
  m.style.left = `${Math.max(8, left)}px`; m.style.top = `${top}px`;
  m.onclick = (e) => { const b = e.target.closest("button"); if (!b) return; closeMenu(); items[b.dataset.i].run(); };
  setTimeout(() => document.addEventListener("click", closeMenu, { once: true }), 0);
}
function closeMenu() { $("#menu")?.remove(); }

/* ---------------- Jobb (installation m.m.) ---------------- */

function watchJob(jobId, { title, onDone } = {}) {
  const body = openModal(`<h3>${esc(title || "Arbetar ...")}</h3>
    <div class="progress" id="jp"><i></i></div><div class="term" id="jl"></div>
    <div class="actions"><button class="btn" data-close id="jc">Kör i bakgrunden</button></div>`);
  let stopped = false;
  const tick = async () => {
    if (stopped || !document.body.contains(body) || !$("#jl", body)) return;
    try {
      const j = await api(`/api/jobs/${jobId}`);
      const log = $("#jl", body); if (!log) return;
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
      log.textContent = j.log.join("\n");
      if (atBottom) log.scrollTop = log.scrollHeight;
      if (j.state !== "running") {
        stopped = true;
        $("#jp", body).className = `progress ${j.state}`;
        $("#jc", body).textContent = "Stäng";
        if (j.state === "done") toast(`${j.title}: klart`, "good"); else toast(`${j.title}: ${j.error || "misslyckades"}`, "bad");
        const res = j.result || {};
        if (j.state === "done" && res.snapshot && res.changed !== false && res.reason !== "manual" && !/punkt/i.test(j.title)) {
          const b = document.createElement("button");
          b.className = "btn"; b.textContent = "↶ Ångra det här";
          b.onclick = () => undoTo(res.app, res.snapshot, res.app, onDone);
          $("#jc", body).before(b);
        }
        if (j.state === "failed") {
          const b = document.createElement("button");
          b.className = "btn"; b.textContent = "✨ Fråga AI varför";
          b.onclick = () => openAI(`Varför misslyckades "${j.title}"? Förklara enkelt och säg vad jag kan göra.`, { fresh: true, app: null, extra: j.log.join("\n") });
          $("#jc", body).before(b);
        }
        onDone && onDone(j);
        return;
      }
    } catch (e) { /* försök igen */ }
    setTimeout(tick, 900);
  };
  tick();
}

/* ---------------- Inloggning ---------------- */

let setupMode = false;
function showAuth(setup) {
  setupMode = setup;
  $("#shell").classList.add("hidden");
  $("#ai-fab").classList.add("hidden"); $("#ai-panel").classList.add("hidden");
  $("#auth").classList.remove("hidden");
  $("#auth-text").textContent = setup ? "Välkommen! Välj ett lösenord för din server." : "Logga in för att fortsätta.";
  $("#auth-btn").textContent = setup ? "Kom igång" : "Logga in";
  $("#auth-pw2").classList.toggle("hidden", !setup);
  $("#auth-perks").classList.toggle("hidden", !setup);
  $("#auth-pw").autocomplete = setup ? "new-password" : "current-password";
  // Familjekonton: välj vem du är
  const people = setup ? [] : (window.WIZZ_PEOPLE || []);
  const box = $("#auth-people");
  if (!people.some((p) => p.id === authUser)) authUser = (people.find((p) => p.id === lsGet("wz-user")) || people[0] || {}).id || "";
  box.classList.toggle("hidden", !people.length);
  box.innerHTML = people.length ? `<p class="hint">Vem är du?</p><div class="who-row">${people.map((p) => `<button type="button" class="who ${p.id === authUser ? "on" : ""}" data-u="${esc(p.id)}"><span>${esc(p.emoji || p.name.charAt(0))}</span>${esc(p.name)}</button>`).join("")}</div>` : "";
  $$("[data-u]", box).forEach((b) => b.onclick = () => { authUser = b.dataset.u; $$("[data-u]", box).forEach((x) => x.classList.toggle("on", x === b)); $("#auth-pw").focus(); });
  const al = $("#auth-lang"); if (al) al.innerHTML = langPicker();
  $("#auth-pw").focus();
}
let authUser = "";
function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* privat läge */ } }
$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pw = $("#auth-pw").value, err = $("#auth-err");
  err.textContent = "";
  try {
    if (setupMode) {
      if (pw.length < 8) throw new Error("Välj minst 8 tecken.");
      if (pw !== $("#auth-pw2").value) throw new Error("Lösenorden är inte lika.");
      await api("/api/setup", { method: "POST", body: { password: pw } });
    } else {
      await api("/api/login", { method: "POST", body: { password: pw, user: authUser } });
      if (authUser) lsSet("wz-user", authUser);
    }
    $("#auth-pw").value = ""; $("#auth-pw2").value = "";
    startApp();
  } catch (ex) { err.textContent = ex.message; }
});

/* ---------------- Sidor ---------------- */

let timers = [];
const clearTimers = () => { timers.forEach(clearInterval); timers = []; };
let storeState = { q: "", cat: "", data: null };

const pages = {
  /* ---------- Hem ---------- */
  async home(view) {
    view.innerHTML = `
      <section class="hero fade-in">
        <div class="hero-main">
          <div class="orb" id="orb" aria-hidden="true"><i></i><i></i></div>
          <div class="hero-text">
            <div class="timeline"><div class="clock" aria-live="off"><span id="clk-t">--:--</span><small id="clk-s"></small></div>
              <div class="date" id="clk-d">&nbsp;</div></div>
            <h1 class="hello"><span id="greet"></span>, <span class="grad-text" id="host">…</span></h1>
            <p class="mood" id="mood">Kollar läget …</p>
            <p class="osline" id="osline">&nbsp;</p>
          </div>
          <div id="jobpill"></div>
        </div>
        <button class="hero-search" id="hero-search" type="button"><svg><use href="#i-search"/></svg><span>Sök i appar, filer, inställningar och på webben …</span><kbd>Ctrl K</kbd></button>
        <div class="quick">
          <a class="qa" href="#/store"><span>＋</span>Lägg till app</a>
          <a class="qa" href="#/store" id="qa-trial"><span>🧪</span>Prova en app</a>
          <a class="qa" href="#/files"><span>📁</span>Filer</a>
          <button class="qa" id="qa-card" type="button"><span>🪪</span>Dela din server</button>
          <button class="qa" id="qa-phone" type="button"><span>📱</span>Till mobilen</button>
          <button class="qa ai-sw hidden" id="qa-ai" type="button" role="switch" aria-checked="false"><span>🤖</span><b>AI</b><i class="sw"><i></i></i></button>
        </div>
      </section>
      <div id="approvals"></div>
      <div id="onboard"></div>
      <div id="dockerwarn"></div>
      <div class="stats fade-in">
        ${["cpu", "mem", "disk"].map((k) => `<div class="card stat"><div class="ring" id="r-${k}"><b>–</b></div>
          <div><div class="k">${{ cpu: "Processor", mem: "Minne", disk: "Lagring" }[k]}</div><div class="v" id="v-${k}">–</div><div class="s" id="s-${k}"></div></div></div>`).join("")}
        <div class="card stat"><div class="ring" style="--p:100;--c:var(--accent2)"><b>⏱</b></div>
          <div><div class="k">Drifttid</div><div class="v" id="v-up">–</div><div class="s" id="s-up"></div></div></div>
        <a class="card stat" href="#/backup" style="text-decoration:none"><div class="ring" id="r-bk" style="--p:100;--c:var(--muted)"><b>🛡️</b></div>
          <div><div class="k">Backup</div><div class="v" id="v-bk">–</div><div class="s" id="s-bk"></div></div></a>
        <button class="card stat" id="costcard" type="button" style="text-align:left;font:inherit;color:inherit;cursor:pointer"><div class="ring" style="--p:100;--c:var(--warn)"><b>⚡</b></div>
          <div><div class="k">Elkostnad</div><div class="v" id="v-cost">–</div><div class="s" id="s-cost">räknar …</div></div></button>
        <a class="card stat hidden" id="nwcard" target="_blank" rel="noopener" style="text-decoration:none"><div class="ring" id="r-nw" style="--p:0;--c:var(--accent)"><b>–</b></div>
          <div><div class="k">Hela hemmet</div><div class="v" id="v-nw">–</div><div class="s" id="s-nw">via Nestward</div></div></a>
      </div>
      <h2>Mina appar <span class="count" id="appcount">0</span></h2>
      <div id="trials"></div>
      <div class="apps" id="apps"></div>
      <div id="removed"></div>
      <div id="others"></div>`;
    $("#greet").textContent = greeting();
    const hs = { sys: null, apps: null };
    $("#qa-card").onclick = shareCard;
    $("#costcard").onclick = () => costModal();
    $("#hero-search").onclick = () => openFinder();
    aiSwitch($("#qa-ai"));
    drawApprovals(); timers.push(setInterval(drawApprovals, 5000));
    $("#qa-phone").onclick = async () => { let url = location.origin; try { const px = await api("/api/proxy"); if (px.enabled && px.wizz_url) url = px.wizz_url; } catch { /* */ } markDone("phone"); phoneModal(url, "WizzOS", "/static/logo-192.png"); };
    $("#qa-trial").onclick = () => { storeState.trialHint = true; };
    tickClock(); timers.push(setInterval(tickClock, 1000));
    const loadStats = async () => {
      const s = await api("/api/system");
      hs.sys = s; drawMood(hs);
      $("#host").textContent = s.hostname;
      $("#osline").textContent = [s.os, s.arch, s.docker.ok ? `Docker ${s.docker.version}` : ""].filter(Boolean).join(" · ");
      $("#dockerwarn").innerHTML = (s.docker.ok ? "" : `<div class="warnbar">Docker svarar inte: ${esc(s.docker.version)}</div>`)
        + (s.problems || []).map((p) => `<div class="warnbar"><b>⚠️ ${esc(p.title)}</b><br><span style="font-size:14px">${esc(p.message)}</span>
          <div style="margin-top:8px"><button class="btn" data-ask="${esc(p.title + ": " + p.message)}">✨ Fråga AI vad jag ska göra</button></div></div>`).join("");
      $$("#dockerwarn [data-ask]").forEach((b) => b.onclick = () => openAI(`Det här problemet visas i WizzOS: "${b.dataset.ask}". Vad betyder det och vad ska jag göra?`, { fresh: true, app: null }));
      const ring = (id, p, warn) => { const r = $(`#r-${id}`); r.style.setProperty("--p", p); r.style.setProperty("--c", p > 90 ? "var(--bad)" : p > warn ? "var(--warn)" : id === "disk" ? "var(--accent2)" : "var(--accent)"); $("b", r).textContent = `${Math.round(p)}%`; };
      ring("cpu", s.cpu, 75); $("#v-cpu").textContent = `${s.cores} kärnor`; $("#s-cpu").textContent = s.temp ? `${s.temp} °C · last ${s.load[0]}` : `last ${s.load[0]}`;
      ring("mem", s.mem.percent, 80); $("#v-mem").textContent = `${fmtBytes(s.mem.used)}`; $("#s-mem").textContent = `av ${fmtBytes(s.mem.total)}`;
      ring("disk", s.disk.percent, 85); $("#v-disk").textContent = `${fmtBytes(s.disk.used)}`; $("#s-disk").textContent = `av ${fmtBytes(s.disk.total)}`;
      const b = s.backup, br = $("#r-bk");
      if (!b.configured) { $("#v-bk").textContent = "Inte inställd"; $("#s-bk").textContent = "Klicka för att komma igång"; br.style.setProperty("--c", "var(--warn)"); }
      else if (!b.last_run) { $("#v-bk").textContent = "Väntar"; $("#s-bk").textContent = `första körningen kl ${b.time}`; br.style.setProperty("--c", "var(--accent)"); }
      else {
        const ok = b.last_run.ok && (!b.last_test || b.last_test.ok);
        $("#v-bk").textContent = ok ? "Allt säkert" : "Kolla backup";
        $("#s-bk").textContent = `${timeAgo(b.last_run.time)}${b.last_test ? (b.last_test.ok ? " · testad ✓" : " · test misslyckades") : ""}`;
        br.style.setProperty("--c", ok ? "var(--good)" : "var(--bad)");
      }
      $("#v-up").textContent = fmtUptime(s.uptime); $("#s-up").textContent = "sedan senaste omstart";
      $("#jobpill").innerHTML = s.jobs.length ? `<span class="jobs-pill" id="jp-open"><span class="spin"></span>${esc(s.jobs[0].title)}</span>` : "";
      if (s.jobs.length) $("#jp-open").onclick = () => watchJob(s.jobs[0].id, { title: s.jobs[0].title, onDone: loadApps });
    };
    const loadApps = async () => {
      const [d, rem, upd] = await Promise.all([api("/api/apps"), api("/api/removed"), api("/api/updates").catch(() => ({ available: {} }))]);
      hs.apps = d.apps; drawMood(hs); drawOnboard(hs);
      $("#removed").innerHTML = rem.length ? `<h2>Nyligen borttagna <span class="count">${rem.length}</span></h2>
        <div class="card list">${rem.map((r) => `<div class="row"><div><div class="n">${esc(r.title)}</div>
          <div class="m">Borttagen ${esc(timeAgo(r.removed))} · kan fås tillbaka till ${esc(new Date(r.expires * 1000).toLocaleDateString(loc()))}</div></div>
          <div class="acts"><button class="btn" data-back="${esc(r.name)}" data-sid="${esc(r.snapshot)}" data-title="${esc(r.title)}">↶ Få tillbaka</button></div></div>`).join("")}</div>` : "";
      $$("#removed [data-back]").forEach((b) => b.onclick = () => undoTo(b.dataset.back, b.dataset.sid, b.dataset.title, loadApps));
      $("#appcount").textContent = d.apps.length;
      const trials = d.apps.filter((a) => a.trial);
      $("#trials").innerHTML = trials.length ? `<div class="card trial-bar fade-in">${trials.map((a) => `<div class="trial-row">
          ${icon(a.icon, a.title)}<div class="grow"><b>${esc(a.title)}</b> är på prov
          <div class="m">Tas bort spårlöst om <b data-until="${a.trial.until}">…</b></div></div>
          <div class="acts"><button class="btn primary" data-keep="${esc(a.name)}">♥ Behåll</button><button class="btn" data-ext="${esc(a.name)}">+1 h</button>
          <button class="btn danger" data-end="${esc(a.name)}">Ta bort nu</button></div></div>`).join("")}</div>` : "";
      $$("#trials [data-keep]").forEach((b) => b.onclick = () => trialAct(d.apps.find((x) => x.name === b.dataset.keep), "keep", loadApps));
      $$("#trials [data-ext]").forEach((b) => b.onclick = () => trialAct(d.apps.find((x) => x.name === b.dataset.ext), "extend", loadApps));
      $$("#trials [data-end]").forEach((b) => b.onclick = () => trialAct(d.apps.find((x) => x.name === b.dataset.end), "end", loadApps));
      $("#apps").innerHTML = d.apps.map((a) => `
        <div class="card tile fade-in ${a.trial ? "is-trial" : ""}" data-name="${esc(a.name)}" tabindex="0" title="${esc(a.url ? new URL(a.url).hostname : "")}">
          <button class="icon-btn menu-btn" aria-label="Fler val"><svg><use href="#i-dots"/></svg></button>
          ${a.trial ? `<span class="trial-tag">🧪 <span data-until="${a.trial.until}" data-short></span></span>` : ""}
          ${!a.trial && upd.available[a.name] ? `<span class="trial-tag new-tag">🆕 Ny version</span>` : ""}
          ${icon(a.icon, a.title)}
          <div class="t">${esc(a.title)}</div>
          <div class="st">${a.asleep ? '<span class="dot paused"></span>💤 Sover' : `<span class="dot ${esc(a.status)}"></span>${STATUS[a.status] || esc(a.status)}`}</div>
        </div>`).join("") + `<a class="card tile add" href="#/store"><svg><use href="#i-plus"/></svg><div class="t">Lägg till app</div></a>`;
      $$("#apps .tile[data-name]").forEach((el) => {
        const a = d.apps.find((x) => x.name === el.dataset.name);
        el.onclick = (e) => { if (e.target.closest(".menu-btn")) { e.stopPropagation(); appMenu(e.target.closest(".menu-btn"), a, loadApps); } else openApp(a); };
        el.onkeydown = (e) => { if (e.key === "Enter") openApp(a); };
        el.oncontextmenu = (e) => { e.preventDefault(); appMenu(el, a, loadApps); };
      });
      $("#others").innerHTML = d.others.length ? `<h2>Andra containrar <span class="count">${d.others.length}</span></h2>
        <p class="hint" style="margin:-8px 0 12px">Saker som redan kör i Docker på den här datorn men inte installerats via WizzOS.</p>
        <div class="card list">${d.others.map((c) => `<div class="row"><div><div class="n"><span class="dot ${esc(c.state)}"></span>${esc(c.name)}</div>
          <div class="m">${esc(c.image)} · ${esc(c.status)}${c.ports.length ? " · port " + c.ports.join(", ") : ""}</div></div>
          <div class="acts">${c.ports.length ? `<a class="icon-btn" title="Öppna" target="_blank" rel="noopener" href="${location.protocol}//${location.hostname}:${c.ports[0]}"><svg><use href="#i-open"/></svg></a>` : ""}
          <button class="icon-btn" data-c="${esc(c.id)}" aria-label="Fler val"><svg><use href="#i-dots"/></svg></button></div></div>`).join("")}</div>` : "";
      $$("#others [data-c]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); containerMenu(b, d.others.find((c) => c.id === b.dataset.c), loadApps); });
    };
    const loadNestward = async () => {
      const n = await api("/api/nestward/summary"); const card = $("#nwcard"); if (!card) return;
      if (!n.configured) { card.classList.add("hidden"); return; }
      card.classList.remove("hidden"); card.href = safeUrl(n.url) || "#/settings";
      const r = $("#r-nw");
      if (!n.ok) { $("#v-nw").textContent = "Ingen kontakt"; $("#s-nw").textContent = n.error || ""; r.style.setProperty("--c", "var(--muted)"); return; }
      r.style.setProperty("--p", n.score); r.style.setProperty("--c", n.score >= 85 ? "var(--good)" : n.score >= 60 ? "var(--warn)" : "var(--bad)");
      $("b", r).textContent = n.score; $("#v-nw").textContent = n.grade_text || `${n.score}/100`;
      $("#s-nw").textContent = `${n.machines} maskiner · ${n.up} uppe${n.down ? ` · ${n.down} nere` : ""}`;
      card.title = n.items.length ? n.items.map((i) => "• " + i.text).join("\n") : (n.good || []).join("\n");
    };
    await Promise.all([loadStats(), loadApps(), loadNestward().catch(() => {}), drawCostCard()]);
    setTimeout(() => view.classList.add("settled"), 600); // uppdateringar efter detta spelar inte infadningen igen
    timers.push(setInterval(() => loadNestward().catch(() => {}), 60000));
    timers.push(setInterval(() => drawCostCard(), 60000));
    timers.push(setInterval(() => loadStats().catch(() => {}), 3000));
    timers.push(setInterval(() => loadApps().catch(() => {}), 15000));
    tickCountdowns(); timers.push(setInterval(tickCountdowns, 1000));
  },

  /* ---------- Appbutik ---------- */
  async store(view) {
    view.innerHTML = `
      <div class="head fade-in"><div><h1>Appbutik</h1><p id="storeinfo">Laddar …</p></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn" id="migrate">📦 Flytta in från Unraid</button>
          <button class="btn" id="custom"><svg><use href="#i-box"/></svg>Egen app</button>
          <button class="btn" id="refresh"><svg><use href="#i-refresh"/></svg>Uppdatera listan</button></div></div>
      <div class="store-hero fade-in"><div class="sh-ico">🧪</div><div><b>Osäker på en app? Prova först.</b>
        <span>Välj en app och tryck på <b>Prova</b>. Den kör som vanligt i upp till tre timmar och försvinner sedan spårlöst – om du inte trycker på Behåll.</span></div></div>
      <div class="toolbar"><div class="search"><svg><use href="#i-search"/></svg><input id="q" placeholder="Sök bland appar … t.ex. bilder, filmer, lösenord" value="${esc(storeState.q)}" autocomplete="off"></div></div>
      <div class="chips" id="chips"></div>
      <div class="store-grid" id="grid"></div>`;
    $("#refresh").onclick = async () => {
      const r = await api("/api/store/refresh", { method: "POST" });
      watchJob(r.job, { title: "Uppdaterar appbutiker", onDone: () => { storeState.data = null; route(); } });
    };
    $("#custom").onclick = customApp;
    $("#migrate").onclick = migrateUnraid;
    if (!storeState.data) storeState.data = await api("/api/store");
    const d = storeState.data;
    const total = d.apps.length;
    $("#storeinfo").textContent = total ? `${total} appar från ${d.stores.length} ${d.stores.length === 1 ? "butik" : "butiker"}` : "Butikerna hämtas – det tar bara någon minut första gången.";
    if (!total) { setTimeout(() => { if (location.hash.startsWith("#/store")) { storeState.data = null; route(); } }, 5000); }
    const draw = () => {
      const q = storeState.q.toLowerCase().trim();
      const list = d.apps.filter((a) => (!storeState.cat || a.category === storeState.cat) &&
        (!q || `${a.title} ${a.tagline} ${a.developer} ${a.category}`.toLowerCase().includes(q)));
      $("#chips").innerHTML = [["", total], ...d.categories].map(([c, n]) => `<button class="chip ${storeState.cat === c ? "on" : ""}" data-c="${esc(c)}">${c ? esc(c) : "Alla"} <span style="opacity:.6">${n}</span></button>`).join("");
      $$("#chips .chip").forEach((b) => b.onclick = () => { storeState.cat = b.dataset.c; draw(); });
      $("#grid").innerHTML = list.length ? list.slice(0, 400).map((a) => `
        <div class="card sapp" data-k="${esc(a.key)}">${icon(a.icon, a.title)}
          <div style="min-width:0"><div class="t">${esc(a.title)}${a.installed ? '<span class="badge ok">Installerad</span>' : ""}${a.supported ? "" : '<span class="badge no">Passar inte</span>'}</div>
          <div class="d">${esc(a.tagline || a.category)}</div></div></div>`).join("") : `<div class="empty">Inga appar matchar.</div>`;
      $$("#grid .sapp").forEach((el) => el.onclick = () => appDetails(el.dataset.k));
    };
    $("#q").oninput = (e) => { storeState.q = e.target.value; draw(); };
    draw();
    if (!storeState.q) $("#q").focus({ preventScroll: true });
  },

  /* ---------- Backup ---------- */
  async backup(view) {
    const d = await api("/api/backup");
    const c = d.config;
    if (!c.configured) return backupSetup(view, c);
    const s = d.summary, run = s.last_run, lt = s.last_test;
    const good = run && run.ok && (!lt || lt.ok);
    const testBadge = (a) => !a.last_test ? `<span class="badge">Inte testad än</span>`
      : a.test_ok ? `<span class="badge ok" title="${esc(a.test_msg || "")}">Återställning testad ${esc(timeAgo(a.last_test))} ✓</span>`
      : `<span class="badge no" title="${esc(a.test_msg || "")}">Testet misslyckades</span>`;
    view.innerHTML = `
      <div class="head fade-in"><div><h1>Backup</h1><p>${esc(c.target)} · varje natt kl ${esc(c.time)}${c.enabled ? "" : " · <b>avstängd</b>"}</p></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" id="bkmove">📦 Flytta till ny server</button><button class="btn" id="bkkey">🔑 Återställningsnyckel</button><button class="btn primary" id="bknow">Säkerhetskopiera nu</button></div></div>
      <div class="card bk-hero fade-in ${!run ? "" : good ? "ok" : "bad"}">
        <div class="bk-shield"><svg><use href="#i-shield"/></svg></div>
        <div>
          <h3>${!run ? "Väntar på första backupen" : good ? "Allt är säkerhetskopierat" : "Något behöver kollas"}</h3>
          <p>${run ? `Senaste backup ${esc(timeAgo(run.time))} – ${run.apps} ${run.apps === 1 ? "app" : "appar"} på ${run.duration} s${run.failed.length ? ` · <b>fel: ${run.failed.map(esc).join(", ")}</b>` : ""}.` : `Första körningen sker i natt kl ${esc(c.time)}, eller tryck "Säkerhetskopiera nu".`}</p>
          ${s.repo_size != null ? `<div class="bk-usage"><div class="bk-bar"><i style="width:${s.max_gb ? Math.min(100, s.repo_size / (s.max_gb * 1073741824) * 100) : 0}%"></i></div>
            <span>Använt ${fmtBytes(s.repo_size)}${s.max_gb ? ` av taket ${s.max_gb} GB` : " (inget tak)"}</span></div>` : ""}
          <p>${lt ? `Senaste återställningstest ${esc(timeAgo(lt.time))}: ${lt.ok ? "appen startade med den återställda datan ✓" : "<b>misslyckades</b>"}` : "Varje natt återställs en app på prov i en sandlåda – så vet du att backupen faktiskt fungerar."}</p>
        </div>
      </div>
      <div id="safety"></div>
      <h2>Appar <span class="count">${d.apps.length}</span></h2>
      <div class="card list">${d.apps.map((a) => `<div class="row bk-row">
          <div class="bk-app">${icon(a.icon, a.title)}<div><div class="n">${esc(a.title)}</div>
            <div class="m">${a.last_backup ? (a.backup_ok ? `Backup ${esc(timeAgo(a.last_backup))} · ${fmtBytes(a.size || 0)}` : `<span style="color:var(--bad)">Backup misslyckades: ${esc(a.backup_msg || "")}</span>`) : "Ingen backup än"}</div>
            <div style="margin-top:4px">${testBadge(a)}</div></div></div>
          <div class="acts"><button class="icon-btn" data-bk="${esc(a.name)}" aria-label="Fler val"><svg><use href="#i-dots"/></svg></button></div></div>`).join("") || `<div class="empty">Inga appar installerade än.</div>`}</div>
      <div id="bkavail"></div>
      <div class="card settings-inline fade-in"><h2>Schema</h2>
        <div class="grid2"><div><label for="bkt">Tid varje natt</label><input id="bkt" type="time" value="${esc(c.time)}"></div>
        <div><label for="bkcap">Tak (GB, 0 = inget)</label><input id="bkcap" type="number" min="0" step="0.5" value="${esc(c.max_gb || 0)}">
          <p class="hint">Passeras taket laddas inget mer upp – du får en varning i stället för en räkning.</p></div>
        <div><label>&nbsp;</label><label class="radio" style="margin:0"><input type="checkbox" id="bkon" ${c.enabled ? "checked" : ""}><div><b>Backup påslagen</b><span>Behåller 7 dagliga, 4 veckovisa och 6 månatliga.</span></div></label></div></div>
        <div class="actions"><button class="btn" id="bksave">Spara</button></div></div>`;
    const reload = () => route();
    $("#bknow").onclick = async () => { try { const r = await api("/api/backup/run", { method: "POST" }); watchJob(r.job, { title: "Backup av alla appar", onDone: reload }); } catch (e) { toast(e.message, "bad"); } };
    $("#bkkey").onclick = revealKey;
    $("#bkmove").onclick = moveOut;
    $("#bksave").onclick = async () => { try { await api("/api/backup/settings", { method: "PUT", body: { time: $("#bkt").value, enabled: $("#bkon").checked, max_gb: parseFloat($("#bkcap").value || "0") } }); toast("Sparat", "good"); reload(); } catch (e) { toast(e.message, "bad"); } };
    $$("[data-bk]").forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      const a = d.apps.find((x) => x.name === b.dataset.bk);
      const job = async (what, title) => { try { const r = await api(`/api/backup/apps/${a.name}/${what}`, { method: "POST" }); watchJob(r.job, { title, onDone: reload }); } catch (err) { toast(err.message, "bad"); } };
      openMenu(b, [
        { label: "Säkerhetskopiera nu", run: () => job("backup", `Backup av ${a.title}`) },
        { label: "Testa återställning nu", run: () => job("test", `Återställningstest av ${a.title}`) },
        "-",
        { label: "Återställ från backup …", run: () => restoreFromBackup(a.name, a.title, reload) },
      ]);
    });
    safetyCard($("#safety"), reload);
    // Appar som finns i backupen men inte här (t.ex. ny server)
    api("/api/backup/available").then((av) => {
      const missing = av.filter((x) => !x.installed);
      if (!missing.length || !$("#bkavail")) return;
      $("#bkavail").innerHTML = `<h2>I backupen men inte installerade här <span class="count">${missing.length}</span></h2>
        <div class="card list">${missing.map((x) => `<div class="row"><div><div class="n">${esc(x.name)}</div><div class="m">Senaste backup ${esc(fmtDate(Date.parse(x.latest) / 1000))}</div></div>
        <div class="acts"><button class="btn" data-avail="${esc(x.name)}">↶ Återställ hit</button></div></div>`).join("")}</div>`;
      $$("[data-avail]").forEach((b) => b.onclick = () => restoreFromBackup(b.dataset.avail, b.dataset.avail, reload));
    }).catch(() => {});
  },

  /* ---------- Inställningar ---------- */
  files(view) { return filesPage(view); },

  async settings(view) {
    const [s, sys, sug, px, aic, al, nw, lt, up, ha] = await Promise.all([api("/api/settings"), api("/api/system"), api("/api/stores/suggested"), api("/api/proxy"), api("/api/ai"), api("/api/alerts"), api("/api/nestward"), api("/api/letter"), api("/api/updates"), api("/api/homeassistant")]);
    const pxIp = px.ip || px.suggested_ip || (/^\d+\.\d+\.\d+\.\d+$/.test(location.hostname) ? location.hostname : "");
    const storeData = storeState.data || await api("/api/store");
    const bindMode = !s.bind_ip ? "all" : s.bind_ip === "127.0.0.1" ? "local" : "custom";
    view.innerHTML = `
      <div class="head fade-in"><div><h1>Inställningar</h1><p>Allt sparas direkt på servern.</p></div>
        <button class="btn" id="wzphone">📱 WizzOS i mobilen</button></div>
      <div class="settings fade-in">
        <div class="card"><h2>Servern</h2>
          <div class="grid2">
            <div><label for="sn">Namn som visas</label><input id="sn" value="${esc(s.server_name)}" placeholder="${esc(sys.hostname)}"></div>
            <div><label for="tz">Tidszon</label><input id="tz" value="${esc(s.tz)}" placeholder="Europe/Stockholm"></div>
            <div><label for="pu">Användar-id (PUID)</label><input id="pu" value="${esc(s.puid)}" inputmode="numeric"></div>
            <div><label for="pg">Grupp-id (PGID)</label><input id="pg" value="${esc(s.pgid)}" inputmode="numeric"></div>
          </div>
          <p class="hint">PUID och PGID styr vilken användare appar sparar filer som. 1000 är den första användaren på de flesta Linux-datorer.</p>
          <h2>Vem når apparna?</h2>
          <label class="radio"><input type="radio" name="bind" value="all" ${bindMode === "all" ? "checked" : ""}><div><b>Alla i nätverket</b><span>Bra hemma bakom routern.</span></div></label>
          <label class="radio"><input type="radio" name="bind" value="local" ${bindMode === "local" ? "checked" : ""}><div><b>Bara servern själv</b><span>Säkrast på en VPS. Du når apparna via en SSH-tunnel eller ett eget proxy.</span></div></label>
          <label class="radio"><input type="radio" name="bind" value="custom" ${bindMode === "custom" ? "checked" : ""}><div style="flex:1"><b>En viss adress</b><span>T.ex. serverns Tailscale-IP (100.x.x.x) – då når bara dina egna enheter apparna.</span>
            <input id="bip" style="margin-top:8px" value="${bindMode === "custom" ? esc(s.bind_ip) : ""}" placeholder="100.64.0.1"></div></label>
          <p class="hint">Gäller appar du installerar efter ändringen.</p>
          <div class="actions"><button class="btn primary" id="save">Spara</button></div>
        </div>

        <div class="card"><h2>🌐 Språk</h2><p class="hint" style="margin-top:-6px">Gäller den här webbläsaren.</p>${langPicker()}</div>

        <div class="card" id="agentcard"><h2>🤖 WizzOS-agenten</h2><p class="hint">Laddar …</p></div>

        <div class="card" id="famcard"><h2>👪 Familj och gäster</h2><p class="hint">Laddar …</p></div>

        <div class="card" id="selfupd"><h2>⬆️ WizzOS</h2><p class="hint">Laddar …</p></div>

        <div class="card" id="mailcard"><h2>📧 E-post</h2><p class="hint">Laddar …</p></div>

        <div class="card"><h2>🔔 Larm ${al.enabled ? '<span class="badge ok">På</span>' : '<span class="badge">Av</span>'}</h2>
          <p class="hint" style="margin-top:-6px">Du får en notis i mobilen när något går fel – och en till när det är löst. Samma problem larmar bara en gång.
            Bevakas: backup och återställningstest, appar som kraschar eller stannar (inte de du själv stoppat), uppdateringar som backades, full disk, https-certifikat och Docker.</p>
          <label class="radio"><input type="checkbox" id="alon" ${al.enabled ? "checked" : ""}><div><b>Skicka larm</b><span>Via ntfy-appen i mobilen (och/eller en webhook, t.ex. Home Assistant).</span></div></label>
          <div class="grid2">
            <div><label for="alu">ntfy-server</label><input id="alu" value="${esc(al.ntfy_url)}" placeholder="https://ntfy.sh"></div>
            <div><label for="alt">Ämne (topic)</label><input id="alt" value="${esc(al.ntfy_topic)}" placeholder="larm"></div>
            <div><label for="alk">Åtkomstnyckel (token)</label><input id="alk" type="password" autocomplete="off" placeholder="${al.has_token ? "Sparad – lämna tomt för att behålla" : "tk_…"}"></div>
            <div><label for="ald">Larma när disken är full till (%)</label><input id="ald" type="number" min="50" max="99" value="${esc(al.disk_percent)}"></div>
          </div>
          <label for="alw">Webhook (valfritt)</label><input id="alw" value="${esc(al.webhook_url)}" placeholder="https://…/api/webhook/…">
          <div class="actions"><button class="btn" id="altest">Skicka testlarm</button><button class="btn primary" id="alsave">Spara</button></div>
          ${al.history.length ? `<details><summary>Senaste händelser (${al.history.length})</summary><div class="list" style="margin-top:8px">${al.history.map((h) => `<div class="row"><div><div class="n">${h.kind === "problem" ? "⚠️" : h.kind === "löst" ? "✅" : "ℹ️"} <span>${esc(h.title)}</span></div><div class="m">${esc(fmtDate(h.time))} · <span>${esc(h.message)}</span></div></div></div>`).join("")}</div></details>` : ""}
        </div>

        <div class="card"><h2>🌙 Nattliga uppdateringar ${up.config.enabled ? '<span class="badge ok">På</span>' : '<span class="badge">Av</span>'}</h2>
          <p class="hint" style="margin-top:-6px">Varje natt hämtar WizzOS nya versioner av dina appar. Före varje uppdatering sparas en återställningspunkt,
            och efteråt kontrolleras att appen mår bra. Gör den inte det backas den automatiskt – du vaknar till en fungerande server.</p>
          <label class="radio"><input type="checkbox" id="upon" ${up.config.enabled ? "checked" : ""}><div><b>Kör varje natt</b><span>Appar på prov, stoppade appar och de du undantagit hoppas över.</span></div></label>
          <label class="radio"><input type="radio" name="upm" value="auto" ${up.config.mode === "auto" ? "checked" : ""}><div><b>Uppdatera själv – med skyddsnät</b><span>Rekommenderas. Du får en notis om något uppdaterades eller backades.</span></div></label>
          <label class="radio"><input type="radio" name="upm" value="notify" ${up.config.mode === "notify" ? "checked" : ""}><div><b>Bara säg till</b><span>Appar med ny version får ett märke – du uppdaterar själv när det passar.</span></div></label>
          <div class="grid2"><div><label for="upt">Klockan</label><input id="upt" type="time" value="${esc(up.config.time)}"><p class="hint">Gärna efter backupen, så att det finns en färsk kopia.</p></div></div>
          <div class="actions"><button class="btn" id="upcheck">Leta efter nya versioner nu</button><button class="btn" id="upnow">Uppdatera allt nu</button><button class="btn primary" id="upsave">Spara</button></div>
          ${up.runs.length ? `<details><summary>Senaste körningar (${up.runs.length})</summary><div class="list" style="margin-top:8px">${up.runs.map((r) => `<div class="row"><div><div class="n">${r.rolled_back.length ? "🛟" : r.updated.length ? "✅" : r.available.length ? "🆕" : "💤"} ${esc(fmtDate(r.time))}${r.mode === "notify" ? " · bara koll" : ""}</div>
            <div class="m">${esc(updSummary(r))}</div></div></div>`).join("")}</div></details>` : ""}
        </div>

        <div class="card"><h2>💌 Veckobrev ${lt.config.enabled ? '<span class="badge ok">På</span>' : '<span class="badge">Av</span>'}</h2>
          <p class="hint" style="margin-top:-6px">En gång i veckan skriver servern ett kort brev till dig: vad som hänt, vad som är skyddat och ett tips. Det skickas till mobilen via larmkanalen${al.enabled ? "" : " (slå på larmen ovan för att få det i mobilen)"}.</p>
          <label class="radio"><input type="checkbox" id="lton" ${lt.config.enabled ? "checked" : ""}><div><b>Skicka veckobrev</b><span>Lugna veckor blir ett kort "allt är bra".</span></div></label>
          <div class="grid2"><div><label for="ltd">Dag</label><select id="ltd">${["måndag","tisdag","onsdag","torsdag","fredag","lördag","söndag"].map((d, i) => `<option value="${i}" ${lt.config.weekday === i ? "selected" : ""}>${d}</option>`).join("")}</select></div>
            <div><label for="lth">Klockan</label><select id="lth">${Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${lt.config.hour === h ? "selected" : ""}>${String(h).padStart(2, "0")}:00</option>`).join("")}</select></div></div>
          <div class="actions"><button class="btn" id="ltpre">Förhandsvisa</button><button class="btn" id="ltsend">Skicka ett nu</button><button class="btn primary" id="ltsave">Spara</button></div>
          ${lt.letters.length ? `<details><summary>Tidigare brev (${lt.letters.length})</summary><div class="list" style="margin-top:8px">${lt.letters.map((x, i) => `<div class="row"><div><div class="n">${esc(x.title)}</div><div class="m">${esc(fmtDate(x.time))}</div></div><div class="acts"><button class="btn" data-lt="${i}">Läs</button></div></div>`).join("")}</div></details>` : ""}
        </div>

        <div class="card"><h2>🏡 Home Assistant ${ha.enabled ? (ha.ok === false ? '<span class="badge no">Fel</span>' : '<span class="badge ok">Kopplad</span>') : ""}</h2>
          <p class="hint" style="margin-top:-6px">WizzOS skickar sitt läge till Home Assistant varje minut som sensorer – status, appar igång, backup, lagring, nya versioner och hemmets hälsa.
            Visa dem på din instrumentpanel eller använd dem i automationer, t.ex. en lampa som lyser rött när servern har problem. Ingen YAML behövs.</p>
          <label class="radio"><input type="checkbox" id="haon" ${ha.enabled ? "checked" : ""}><div><b>Skicka sensorer till Home Assistant</b><span>${ha.last ? (ha.ok ? `Senast skickat ${esc(timeAgo(ha.last))} – ${ha.count} sensorer.` : `Senaste försöket misslyckades: ${esc(ha.error)}`) : "Inget skickat än."}</span></div></label>
          <div class="grid2">
            <div><label for="hau">Adress till Home Assistant</label><input id="hau" value="${esc(ha.url)}" placeholder="https://homeassistant.exempel.se"></div>
            <div><label for="hak">Långlivad åtkomsttoken</label><input id="hak" type="password" autocomplete="off" placeholder="${ha.has_token ? "Sparad – lämna tomt för att behålla" : "Klistra in token"}"></div>
          </div>
          <p class="hint">Token skapar du i Home Assistant: klicka på ditt namn längst ner till vänster → fliken <b>Säkerhet</b> → <b>Långlivade åtkomsttoken</b> → <b>Skapa token</b>. Den sparas bara på servern.</p>
          <details><summary>Vilka sensorer blir det?</summary><div class="ha-list">
            <code>sensor.wizzos_status</code> ok / varning / problem · <code>sensor.wizzos_appar_igang</code> · <code>sensor.wizzos_backup</code> bevisad / gjord / misslyckad ·
            <code>sensor.wizzos_senaste_backup</code> · <code>sensor.wizzos_lagring</code> · <code>sensor.wizzos_processor</code> · <code>sensor.wizzos_minne</code> ·
            <code>sensor.wizzos_uppdateringar</code> · <code>sensor.wizzos_hemmet</code> (om Nestward är kopplad)</div></details>
          <div class="actions">${ha.url ? '<button class="btn danger" id="haoff">Koppla bort</button>' : ""}<button class="btn primary" id="hasave">Spara och testa</button></div>
        </div>

        <div class="card"><h2>🏠 Nestward ${nw.url && nw.has_token ? '<span class="badge ok">Kopplad</span>' : ""}</h2>
          <p class="hint" style="margin-top:-6px">Nestward är vakttornet som ser <i>alla</i> maskiner i hemmet. Kopplar du ihop dem visas hemmets hälsopoäng här,
            och Nestward räknar WizzOS backup och provåterställning – och larmar inte för appar på prov eller som du stoppat själv.
            Nestward läser WizzOS status automatiskt om en Nestward-agent kör på den här datorn.</p>
          <div class="grid2">
            <div><label for="nwu">Nestwards adress</label><input id="nwu" value="${esc(nw.url)}" placeholder="http://100.x.x.x:8470"></div>
            <div><label for="nwk">Läsnyckel (share.token)</label><input id="nwk" type="password" autocomplete="off" placeholder="${nw.has_token ? "Sparad – lämna tomt för att behålla" : "Minst 24 tecken"}"></div>
          </div>
          <p class="hint">Nyckeln kan bara läsa hälsosammanfattningen. Skapa den i Nestward med <code>nestward config set share.token …</code></p>
          <div class="actions">${nw.url ? '<button class="btn danger" id="nwoff">Koppla bort</button>' : ""}<button class="btn primary" id="nwsave">Spara och testa</button></div>
        </div>

        <div class="card"><h2>✨ AI-hjälp ${aic.configured ? '<span class="badge ok">På</span>' : ""}</h2>
          <p class="hint" style="margin-top:-6px">Assistenten förklarar loggar och fel och tipsar om appar – på svenska. Den ser serverns status men gör inget utan att du trycker. Fungerar med din egen AI-gateway (LiteLLM) eller annan OpenAI-kompatibel tjänst.</p>
          <div class="grid2">
            <div><label for="aiu">Adress</label><input id="aiu" value="${esc(aic.base_url)}" placeholder="https://din-ai-server/v1"></div>
            <div><label for="aim">Modell</label><input id="aim" value="${esc(aic.model)}" placeholder="gratis-chatt"></div>
          </div>
          <label for="aik">API-nyckel</label><input id="aik" type="password" autocomplete="off" placeholder="${aic.has_key ? "Sparad – lämna tomt för att behålla" : "T.ex. LiteLLM-nyckeln"}">
          <details ${aic.has_cf  ? "open" : ""}><summary>Cloudflare Access-servicenyckel (om AI-servern ligger bakom Cloudflare Access)</summary>
            <div class="grid2"><div><label for="aic1">Client ID</label><input id="aic1" value="${esc(aic.cf_id)}" autocomplete="off"></div>
            <div><label for="aic2">Client Secret</label><input id="aic2" type="password" autocomplete="off" placeholder="${aic.has_cf ? "Sparad" : ""}"></div></div></details>
          <p class="hint">Obs: frågor och loggar skickas till AI-tjänsten. Lösenord och nycklar maskeras först, men skicka inget känsligt i själva frågan.</p>
          <div class="actions"><button class="btn" id="aitest">Testa</button><button class="btn primary" id="aisave">Spara</button></div>
        </div>

        <div class="card" id="doccard"><h2>📄 Dokument</h2><p class="hint">Laddar …</p></div>

        <div class="card" id="ailink"><h2>🤖 AI-koppling</h2><p class="hint">Laddar …</p></div>

        <div class="card"><h2>Egen adress (https) ${px.enabled ? `<span class="badge ${px.status === "Aktiv" ? "ok" : ""}">${esc(px.status || "På")}</span>` : ""}</h2>
          <p class="hint" style="margin-top:-6px">Ger varje app en riktig adress med säker anslutning, t.ex. <b>jellyfin.${esc(px.domain || "hem.exempel.se")}</b>, i stället för IP och portnummer. Kräver en domän hos Cloudflare.</p>
          ${px.enabled ? `<p>WizzOS nås på <a href="${esc(px.wizz_url)}" target="_blank" rel="noopener">${esc(px.wizz_url)}</a>, och apparna på <b>&lt;app&gt;.${esc(px.domain)}</b>.</p>` : ""}
          <div class="grid2">
            <div><label for="pxd">Domän</label><input id="pxd" value="${esc(px.domain)}" placeholder="wizz.din-domän.se"></div>
            <div><label for="pxi">Adressen ska peka på IP</label><input id="pxi" value="${esc(pxIp)}" placeholder="100.64.0.1"></div>
          </div>
          <p class="hint">IP:n är oftast serverns Tailscale-adress (100.x.x.x) eller dess adress i hemnätverket (192.168.x.x). Då blir adresserna privata – bara du når dem.</p>
          <label for="pxt">Cloudflare-nyckel (API-token)</label>
          <input id="pxt" type="password" autocomplete="off" placeholder="${px.has_token ? "Sparad – lämna tomt för att behålla" : "Klistra in nyckeln här"}">
          <p class="hint">Nyckeln behöver bara behörigheten <b>Zone → DNS → Edit</b> för din domän. Den sparas bara på servern och visas aldrig igen.</p>
          <div class="actions">
            ${px.enabled ? `<button class="btn" id="pxlog">Visa proxyns logg</button><button class="btn danger" id="pxoff">Stäng av</button>` : ""}
            <button class="btn primary" id="pxon">${px.enabled ? "Spara ändringar" : "Slå på"}</button></div>
        </div>

        <div class="card"><h2>Appbutiker</h2>
          <p class="hint" style="margin-top:-6px">Samma butiker som CasaOS använder. Du kan lägga till fler med en GitHub-adress eller zip-länk.</p>
          <div id="stores">${storeData.stores.map((x) => `<div class="store-row"><div class="grow"><b>${esc(x.name)}</b>
            <div class="m">${x.count} appar · ${esc(x.url)}</div></div>
            <button class="btn danger" data-rm="${esc(x.id)}">Ta bort</button></div>`).join("")}</div>
          ${sug.length ? `<h2 style="font-size:15px">Förslag</h2>${sug.map((x) => `<div class="store-row"><div class="grow"><b>${esc(x.name)}</b><div class="m">${esc(x.url)}</div></div>
            <button class="btn" data-add="${esc(x.url)}" data-name="${esc(x.name)}"><svg><use href="#i-plus"/></svg>Lägg till</button></div>`).join("")}` : ""}
          <div class="grid2" style="margin-top:8px"><div><label for="stn">Namn</label><input id="stn" placeholder="Min butik"></div>
          <div><label for="stu">Adress</label><input id="stu" placeholder="https://github.com/…/archive/refs/heads/main.zip"></div></div>
          <div class="actions"><button class="btn" id="addstore"><svg><use href="#i-plus"/></svg>Lägg till butik</button></div>
        </div>

        <div class="card"><h2>Lösenord</h2>
          <div class="grid2"><div><label for="pw0">Nuvarande</label><input type="password" id="pw0" autocomplete="current-password"></div>
          <div><label for="pw1">Nytt (minst 8 tecken)</label><input type="password" id="pw1" autocomplete="new-password"></div></div>
          <div class="actions"><button class="btn" id="logout">Logga ut</button><button class="btn primary" id="chpw">Byt lösenord</button></div>
        </div>
        <p class="hint">WizzOS ${esc(window.WIZZ_VERSION || "")} · ${esc(sys.os)} · ${esc(sys.arch)} · Docker ${esc(sys.docker.version)}</p>
      </div>`;
    $("#save").onclick = async () => {
      const mode = $("input[name=bind]:checked").value;
      const bind_ip = mode === "all" ? "" : mode === "local" ? "127.0.0.1" : $("#bip").value.trim();
      try {
        await api("/api/settings", { method: "PUT", body: { server_name: $("#sn").value, tz: $("#tz").value, puid: $("#pu").value, pgid: $("#pg").value, bind_ip } });
        toast("Sparat", "good");
      } catch (e) { toast(e.message, "bad"); }
    };
    $("#upsave").onclick = async () => { try { await api("/api/updates", { method: "PUT", body: { enabled: $("#upon").checked, mode: $("input[name=upm]:checked").value, time: $("#upt").value } }); toast("Sparat", "good"); route(); } catch (e) { toast(e.message, "bad"); } };
    $("#upcheck").onclick = async () => { try { const r = await api("/api/updates/run", { method: "POST", body: { mode: "notify" } }); watchJob(r.job, { title: "Letar efter nya versioner", onDone: () => route() }); } catch (e) { toast(e.message, "bad"); } };
    $("#upnow").onclick = async () => {
      const ok = await confirmBox("Uppdatera alla appar nu?", "Varje app får en återställningspunkt först och backas automatiskt om den nya versionen inte fungerar. Apparna startar om en kort stund var.", "Uppdatera nu");
      if (!ok) return;
      try { const r = await api("/api/updates/run", { method: "POST", body: { mode: "auto" } }); watchJob(r.job, { title: "Uppdaterar alla appar", onDone: () => route() }); } catch (e) { toast(e.message, "bad"); }
    };
    $("#ltsave").onclick = async () => { try { await api("/api/letter", { method: "PUT", body: { enabled: $("#lton").checked, weekday: +$("#ltd").value, hour: +$("#lth").value } }); toast("Sparat", "good"); route(); } catch (e) { toast(e.message, "bad"); } };
    $("#ltpre").onclick = async () => { try { showLetter(await api("/api/letter/preview", { method: "POST" }), true); } catch (e) { toast(e.message, "bad"); } };
    $("#ltsend").onclick = async () => { try { const r = await api("/api/letter/send", { method: "POST" }); toast(r.sent ? "Veckobrevet är skickat – kolla mobilen" : (r.error || "Sparat, men larmen är inte påslagna"), r.sent ? "good" : "bad"); showLetter(r); } catch (e) { toast(e.message, "bad"); } };
    $$("[data-lt]").forEach((b) => b.onclick = () => showLetter(lt.letters[+b.dataset.lt]));
    $("#wzphone").onclick = () => phoneModal(px.enabled && px.wizz_url ? px.wizz_url : location.origin, "WizzOS", "/static/logo-192.png");
    $("#hasave").onclick = async () => {
      try {
        await api("/api/homeassistant", { method: "PUT", body: { enabled: $("#haon").checked || !ha.url, url: $("#hau").value, token: $("#hak").value } }); $("#hak").value = "";
        const r = await api("/api/homeassistant/test", { method: "POST" });
        toast(`Kopplad! ${r.sensors} sensorer finns nu i Home Assistant`, "good"); route();
      } catch (e) { toast(e.message, "bad"); }
    };
    $("#haoff")?.addEventListener("click", async () => { try { await api("/api/homeassistant", { method: "PUT", body: { enabled: false, url: "" } }); toast("Bortkopplad", "good"); route(); } catch (e) { toast(e.message, "bad"); } });
    $("#nwsave").onclick = async () => {
      try {
        await api("/api/nestward", { method: "PUT", body: { url: $("#nwu").value, token: $("#nwk").value } }); $("#nwk").value = "";
        const r = await api("/api/nestward/summary?force=1");
        if (r.ok) toast(`Kopplad! Hemmets hälsa: ${r.score}/100`, "good"); else toast(r.error || "Kunde inte nå Nestward", "bad");
        route();
      } catch (e) { toast(e.message, "bad"); }
    };
    $("#nwoff")?.addEventListener("click", async () => { try { await api("/api/nestward", { method: "PUT", body: { url: "" } }); toast("Bortkopplad", "good"); route(); } catch (e) { toast(e.message, "bad"); } });
    const alBody = () => ({ enabled: $("#alon").checked, ntfy_url: $("#alu").value, ntfy_topic: $("#alt").value, ntfy_token: $("#alk").value, webhook_url: $("#alw").value, disk_percent: parseInt($("#ald").value || "90", 10) });
    mailCard().catch(() => {});
    selfUpdCard().catch(() => {});
    familyCard().catch(() => {});
    agentCard().catch(() => {});
    aiLinkCard().catch(() => {});
    docCard().catch(() => {});
    $("#alsave").onclick = async () => { try { await api("/api/alerts", { method: "PUT", body: alBody() }); $("#alk").value = ""; toast("Larmen är sparade", "good"); route(); } catch (e) { toast(e.message, "bad"); } };
    $("#altest").onclick = async () => {
      try { await api("/api/alerts", { method: "PUT", body: alBody() }); $("#alk").value = ""; await api("/api/alerts/test", { method: "POST" }); toast("Testlarm skickat – kolla mobilen", "good"); }
      catch (e) { toast(e.message, "bad"); }
    };
    const aiBody = () => ({ base_url: $("#aiu").value, model: $("#aim").value, api_key: $("#aik").value, cf_id: $("#aic1").value, cf_secret: $("#aic2").value, enabled: true });
    $("#aisave").onclick = async () => {
      try { await api("/api/ai", { method: "PUT", body: aiBody() }); $("#aik").value = ""; $("#aic2").value = ""; toast("AI-hjälpen är sparad", "good"); route(); }
      catch (e) { toast(e.message, "bad"); }
    };
    $("#aitest").onclick = async () => {
      const b = $("#aitest"); b.disabled = true; b.textContent = "Testar …";
      try {
        await api("/api/ai", { method: "PUT", body: aiBody() }); $("#aik").value = ""; $("#aic2").value = "";
        const r = await api("/api/ai/test", { method: "POST" });
        toast(`Det fungerar! Svar: ${r.reply.slice(0, 60)}`, "good");
      } catch (e) { toast(e.message, "bad"); }
      b.disabled = false; b.textContent = "Testa";
    };
    $("#pxon").onclick = async () => {
      try {
        const r = await api("/api/proxy", { method: "POST", body: { domain: $("#pxd").value, ip: $("#pxi").value, token: $("#pxt").value } });
        $("#pxt").value = "";
        watchJob(r.job, { title: "Egen adress", onDone: () => route() });
      } catch (e) { toast(e.message, "bad"); }
    };
    if ($("#pxoff")) $("#pxoff").onclick = async () => {
      if (!await confirmBox("Stänga av egna adresser?", "Apparna nås då bara via IP och port igen.", "Stäng av", true)) return;
      const r = await api("/api/proxy", { method: "DELETE" }); watchJob(r.job, { title: "Egen adress", onDone: () => route() });
    };
    if ($("#pxlog")) $("#pxlog").onclick = () => showLogs("/api/proxy/logs", "proxy");
    const addStore = async (name, url) => {
      try { const r = await api("/api/stores", { method: "POST", body: { name, url } }); storeState.data = null;
        watchJob(r.job, { title: `Hämtar ${name || "butik"}`, onDone: () => { storeState.data = null; route(); } });
      } catch (e) { toast(e.message, "bad"); }
    };
    $$("[data-add]").forEach((b) => b.onclick = () => addStore(b.dataset.name, b.dataset.add));
    $("#addstore").onclick = () => addStore($("#stn").value, $("#stu").value);
    $$("[data-rm]").forEach((b) => b.onclick = async () => {
      if (!await confirmBox("Ta bort butiken?", "Apparna du redan installerat påverkas inte.", "Ta bort", true)) return;
      await api(`/api/stores/${b.dataset.rm}`, { method: "DELETE" }); storeState.data = null; route();
    });
    $("#chpw").onclick = async () => {
      try { await api("/api/password", { method: "POST", body: { current: $("#pw0").value, password: $("#pw1").value } }); toast("Lösenordet är bytt", "good"); $("#pw0").value = $("#pw1").value = ""; }
      catch (e) { toast(e.message, "bad"); }
    };
    $("#logout").onclick = async () => { await api("/api/logout", { method: "POST" }); showAuth(false); };
  },
};

/* ---------------- Filer ---------------- */

const fs = { path: "/", data: null, sel: new Set(), q: "", clip: null };
const FILE_ICON = [
  [/\.(jpe?g|png|gif|webp|avif|bmp|svg|heic)$/i, "🖼️"], [/\.(mp4|mkv|avi|mov|webm|m4v)$/i, "🎬"],
  [/\.(mp3|flac|wav|ogg|m4a|aac|opus)$/i, "🎵"], [/\.(zip|tar|gz|tgz|7z|rar|xz|bz2)$/i, "📦"],
  [/\.pdf$/i, "📕"], [/\.(ya?ml|json|toml|ini|conf|cfg|env|xml)$/i, "⚙️"],
  [/\.(txt|md|log|csv)$/i, "📝"], [/\.(sh|py|js|ts|php|go|rs|c|cpp|java|rb)$/i, "📜"],
  [/\.(db|sqlite3?)$/i, "🗄️"], [/\.(iso|img|qcow2)$/i, "💿"],
];
const fileIcon = (e) => e.dir ? "📁" : (FILE_ICON.find(([re]) => re.test(e.name)) || [, "📄"])[1];
const encP = (p) => p.split("/").map(encodeURIComponent).join("/");
const joinPath = (dir, name) => (dir === "/" ? "" : dir) + "/" + name;
const isText = (n) => /\.(txt|md|log|csv|ya?ml|json|toml|ini|conf|cfg|env|xml|sh|py|js|ts|php|go|rs|c|cpp|h|java|rb|css|html?|sql|properties|service|cron|gitignore|dockerignore)$|^(dockerfile|makefile|readme|license|\.env.*)$/i.test(n);
const isImage = (n) => /\.(jpe?g|png|gif|webp|avif|bmp)$/i.test(n);
const isMedia = (n) => /\.(mp4|webm|m4v|mov|mp3|flac|wav|ogg|m4a|aac|opus)$/i.test(n);
const dlUrl = (p, inline = false) => `/api/files/download?path=${encodeURIComponent(p)}${inline ? "&inline=1" : ""}`;

function toastAction(text, label, fn, kind = "good") {
  const t = document.createElement("div");
  t.className = `toast ${kind} with-act`;
  t.innerHTML = `<span></span><button class="btn">${esc(label)}</button>`;
  $("span", t).textContent = text;
  $("button", t).onclick = () => { t.remove(); fn(); };
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 9000);
}

async function undoTrash(ids) {
  try {
    for (const id of ids) await api(`/api/trash/${encodeURIComponent(id)}/restore`, { method: "POST" });
    toast("Ångrat – allt är tillbaka", "good");
    if (location.hash.startsWith("#/files")) filesLoad();
  } catch (e) { toast(e.message, "bad"); }
}

async function filesPage(view) {
  const p = decodeURIComponent(location.hash.replace(/^#\/files/, "")) || "/";
  if (p !== fs.path) { fs.sel.clear(); fs.q = ""; }
  fs.path = p;
  view.innerHTML = `
    <div class="head fade-in"><div><h1>Filer</h1><p id="fcrumbs" class="crumbs"></p></div>
      <div class="f-tools">
        <div class="search small"><svg><use href="#i-search"/></svg><input id="fq" placeholder="Filtrera …" value="${esc(fs.q)}" aria-label="Filtrera"></div>
        <button class="btn" id="fmk"><svg><use href="#i-plus"/></svg>Ny mapp</button>
        <label class="btn primary" for="fup">⬆ Ladda upp</label><input type="file" id="fup" multiple hidden>
        <button class="btn" id="ftrash" title="Papperskorg">🗑️ <span id="ftc"></span></button>
      </div></div>
    <div id="fbar"></div>
    <div class="card list f-list fade-in" id="flist"><div class="empty">Läser …</div></div>
    <p class="hint" id="ffree"></p>
    <div class="f-drop hidden" id="fdrop"><div>Släpp filerna för att ladda upp till<br><b id="fdropto"></b></div></div>`;
  $("#fq").oninput = (e) => { fs.q = e.target.value; filesDraw(); };
  $("#fmk").onclick = filesMkdir;
  $("#fup").onchange = (e) => { filesUpload([...e.target.files]); e.target.value = ""; };
  $("#ftrash").onclick = showTrash;
  // Dra och släpp filer var som helst på sidan.
  let depth = 0;
  const drop = $("#fdrop");
  view.ondragenter = (e) => { if (![...(e.dataTransfer?.types || [])].includes("Files")) return; e.preventDefault(); depth++; $("#fdropto").textContent = fs.path; drop.classList.remove("hidden"); };
  view.ondragover = (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) e.preventDefault(); };
  view.ondragleave = () => { if (--depth <= 0) { depth = 0; drop.classList.add("hidden"); } };
  view.ondrop = (e) => { e.preventDefault(); depth = 0; drop.classList.add("hidden"); const f = [...(e.dataTransfer?.files || [])]; if (f.length) filesUpload(f); };
  await filesLoad();
}

async function filesLoad() {
  try {
    const [d, t] = await Promise.all([api(`/api/files?path=${encodeURIComponent(fs.path)}`), api("/api/trash")]);
    fs.data = d;
    if (d.path !== fs.path) { fs.path = d.path; }
    const tc = $("#ftc"); if (tc) tc.textContent = t.items.length || "";
  } catch (e) {
    const l = $("#flist"); if (l) l.innerHTML = `<div class="empty">${esc(e.message)}<br><br><a class="btn" href="#/files">Till början</a></div>`;
    return;
  }
  filesDraw();
}

function filesDraw() {
  const d = fs.data; const list = $("#flist"); if (!d || !list) return;
  // Brödsmulor
  const parts = d.path.split("/").filter(Boolean);
  let acc = "";
  $("#fcrumbs").innerHTML = `<a href="#/files">💾 Lagring</a>` + parts.map((x) => { acc += "/" + x; return ` <span>›</span> <a href="#/files${esc(encP(acc))}">${esc(x)}</a>`; }).join("");
  $("#ffree").textContent = `${fmtBytes(d.free)} ledigt av ${fmtBytes(d.total)} · Raderat hamnar i papperskorgen i 30 dagar.`;
  const q = fs.q.trim().toLowerCase();
  const rows = d.entries.filter((e) => !q || e.name.toLowerCase().includes(q));
  const up = d.path !== "/" ? `<a class="row f-row f-up" href="#/files${esc(encP(d.path.replace(/\/[^/]+$/, "") || "/"))}"><span class="f-ico">↩</span><div class="n">Upp en nivå</div></a>` : "";
  const appNote = d.app ? `<div class="f-appnote">${icon(d.app.icon, d.app.title)}<div>Här ligger <b>${esc(d.app.title)}</b>s data. Ändra försiktigt – och stoppa gärna appen först. Gör du fel finns papperskorgen och appens ↶ Ångra.</div></div>` : "";
  list.innerHTML = appNote + up + (rows.map((e) => {
    const p = joinPath(d.path, e.name);
    const meta = e.dir ? (e.app ? `Data för ${esc(e.app.title)}` : "Mapp") : `${fmtBytes(e.size)}`;
    return `<div class="row f-row ${fs.sel.has(p) ? "sel" : ""}" data-p="${esc(p)}">
      <label class="f-check" title="Markera"><input type="checkbox" ${fs.sel.has(p) ? "checked" : ""} aria-label="Markera ${esc(e.name)}"></label>
      ${e.app ? `<span class="f-ico app">${icon(e.app.icon, e.app.title)}</span>` : `<span class="f-ico">${fileIcon(e)}</span>`}
      <button class="f-name" type="button"><div class="n">${esc(e.name)}${e.link ? ' <span class="badge">länk</span>' : ""}</div>
        <div class="m">${meta} · ${esc(timeAgo(e.mtime))}</div></button>
      <div class="acts"><button class="icon-btn" data-m aria-label="Fler val"><svg><use href="#i-dots"/></svg></button></div></div>`;
  }).join("") || `<div class="empty">${q ? "Inget matchar filtret." : "Mappen är tom. Dra hit filer eller tryck på Ladda upp."}</div>`);
  $$(".f-row[data-p]", list).forEach((r) => {
    const p = r.dataset.p; const e = d.entries.find((x) => joinPath(d.path, x.name) === p);
    $("input", r).onchange = (ev) => { ev.target.checked ? fs.sel.add(p) : fs.sel.delete(p); r.classList.toggle("sel", ev.target.checked); filesBar(); };
    $(".f-name", r).onclick = () => filesOpen(e, p);
    $("[data-m]", r).onclick = (ev) => { ev.stopPropagation(); filesMenu(ev.currentTarget, e, p); };
  });
  filesBar();
}

function filesBar() {
  const bar = $("#fbar"); if (!bar) return;
  const n = fs.sel.size;
  const clip = fs.clip;
  if (!n && !clip) { bar.innerHTML = ""; return; }
  bar.innerHTML = `<div class="card f-bar">
    ${n ? `<b>${n} markerade</b>
      <button class="btn" id="fbdl">⬇ Ladda ner</button>
      <button class="btn" id="fbmv">✂ Flytta</button>
      <button class="btn" id="fbcp">⧉ Kopiera</button>
      <button class="btn danger" id="fbrm">🗑 Radera</button>
      <button class="btn" id="fbnone">Avmarkera</button>` : ""}
    ${clip ? `<span class="f-clip">${clip.copy ? "Kopierar" : "Flyttar"} ${clip.paths.length} st</span>
      <button class="btn primary" id="fbpaste">Lägg här</button><button class="btn" id="fbcancel">Avbryt</button>` : ""}</div>`;
  const sel = () => [...fs.sel];
  if (n) {
    $("#fbdl").onclick = () => sel().forEach((p, i) => setTimeout(() => { const a = document.createElement("a"); a.href = dlUrl(p); a.download = ""; document.body.append(a); a.click(); a.remove(); }, i * 400));
    $("#fbmv").onclick = () => { fs.clip = { paths: sel(), copy: false }; fs.sel.clear(); toast("Gå till mappen där de ska ligga och tryck på Lägg här"); filesDraw(); };
    $("#fbcp").onclick = () => { fs.clip = { paths: sel(), copy: true }; fs.sel.clear(); toast("Gå till mappen där kopiorna ska ligga och tryck på Lägg här"); filesDraw(); };
    $("#fbrm").onclick = () => filesDelete(sel());
    $("#fbnone").onclick = () => { fs.sel.clear(); filesDraw(); };
  }
  if (clip) {
    $("#fbpaste").onclick = async () => {
      try { const r = await api("/api/files/move", { method: "POST", body: { paths: clip.paths, dest: fs.path, as_copy: clip.copy } }); toast(`${clip.copy ? "Kopierade" : "Flyttade"} ${r.count} st`, "good"); fs.clip = null; filesLoad(); }
      catch (e) { toast(e.message, "bad"); }
    };
    $("#fbcancel").onclick = () => { fs.clip = null; filesBar(); };
  }
}

function filesMenu(anchor, e, p) {
  const items = [];
  if (e.dir) items.push({ label: "Öppna", run: () => { location.hash = "#/files" + encP(p); } });
  else if (isText(e.name)) items.push({ label: "Öppna och redigera", run: () => filesEdit(p) });
  else if (isImage(e.name) || isMedia(e.name)) items.push({ label: "Visa", run: () => filesOpen(e, p) });
  items.push({ label: e.dir ? "Ladda ner som zip" : "Ladda ner", run: () => { location.href = dlUrl(p); } });
  if (window.ME?.role === "admin") items.push({ label: "🔗 Dela länk …", run: () => shareModal(p, e.name, e.dir) });
  items.push({ label: "Byt namn …", run: () => filesRename(e, p) });
  items.push({ label: "Flytta …", run: () => { fs.clip = { paths: [p], copy: false }; toast("Gå till mappen där den ska ligga och tryck på Lägg här"); filesBar(); } });
  items.push({ label: "Kopiera …", run: () => { fs.clip = { paths: [p], copy: true }; toast("Gå till mappen där kopian ska ligga och tryck på Lägg här"); filesBar(); } });
  items.push("-");
  items.push({ label: "Radera", danger: true, run: () => filesDelete([p]) });
  openMenu(anchor, items);
}

function filesOpen(e, p) {
  if (e.dir) { location.hash = "#/files" + encP(p); return; }
  if (isText(e.name)) return filesEdit(p);
  if (isImage(e.name)) {
    openModal(`<h3>${esc(e.name)}</h3><p class="hint">${fmtBytes(e.size)}</p><div class="f-view"><img src="${esc(dlUrl(p, true))}" alt="${esc(e.name)}"></div>
      <div class="actions"><a class="btn" href="${esc(dlUrl(p))}">⬇ Ladda ner</a><button class="btn" data-close>Stäng</button></div>`);
    return;
  }
  if (isMedia(e.name)) {
    const tag = /\.(mp3|flac|wav|ogg|m4a|aac|opus)$/i.test(e.name) ? "audio" : "video";
    openModal(`<h3>${esc(e.name)}</h3><div class="f-view"><${tag} src="${esc(dlUrl(p, true))}" controls autoplay></${tag}></div>
      <div class="actions"><a class="btn" href="${esc(dlUrl(p))}">⬇ Ladda ner</a><button class="btn" data-close>Stäng</button></div>`);
    return;
  }
  if (/\.pdf$/i.test(e.name)) { window.open(dlUrl(p, true), "_blank", "noopener"); return; }
  location.href = dlUrl(p);
}

async function filesEdit(p) {
  let f;
  try { f = await api(`/api/files/text?path=${encodeURIComponent(p)}`); } catch (e) { toast(e.message, "bad"); return; }
  const name = p.split("/").pop();
  const body = openModal(`<h3>${esc(name)}</h3><p class="hint">${esc(p)} · Den gamla versionen sparas i papperskorgen när du sparar.</p>
    <textarea id="fed" spellcheck="false"></textarea>
    <div class="actions"><button class="btn" id="fai">✨ Förklara filen</button><button class="btn" data-close>Stäng</button><button class="btn primary" id="fsave">Spara</button></div>`);
  const ta = $("#fed", body); ta.value = f.text;
  ta.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); $("#fsave", body).click(); } });
  $("#fai", body).onclick = () => { closeModal(); openAI(`Förklara enkelt vad filen ${name} gör och vad de viktigaste raderna betyder.`, { fresh: true, app: null, extra: `Fil: ${p}\n\n${f.text.slice(0, 6000)}` }); };
  $("#fsave", body).onclick = async () => {
    try {
      const r = await api("/api/files/text", { method: "PUT", body: { path: p, text: ta.value, mtime: f.mtime } });
      f.mtime = r.mtime; closeModal(); filesLoad();
      toastAction(`${name} är sparad`, "↶ Ångra", () => undoTrash([r.undo]));
    } catch (e) { toast(e.message, "bad"); }
  };
}

async function filesMkdir() {
  const body = openModal(`<h3>Ny mapp</h3><p>Skapas i ${esc(fs.path)}</p><label for="fmn">Namn</label><input id="fmn" autocomplete="off">
    <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="fmok">Skapa</button></div>`);
  const inp = $("#fmn", body); inp.focus();
  const go = async () => { try { await api("/api/files/mkdir", { method: "POST", body: { path: fs.path, name: inp.value } }); closeModal(); filesLoad(); } catch (e) { toast(e.message, "bad"); } };
  $("#fmok", body).onclick = go; inp.onkeydown = (e) => { if (e.key === "Enter") go(); };
}

function filesRename(e, p) {
  const body = openModal(`<h3>Byt namn</h3><label for="frn">Nytt namn</label><input id="frn" autocomplete="off">
    <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="frok">Byt namn</button></div>`);
  const inp = $("#frn", body); inp.value = e.name; inp.focus();
  const dot = e.dir ? -1 : e.name.lastIndexOf(".");
  inp.setSelectionRange(0, dot > 0 ? dot : e.name.length);
  const go = async () => {
    try { await api("/api/files/rename", { method: "POST", body: { path: p, name: inp.value } }); closeModal(); fs.sel.delete(p); filesLoad(); }
    catch (err) { toast(err.message, "bad"); }
  };
  $("#frok", body).onclick = go; inp.onkeydown = (ev) => { if (ev.key === "Enter") go(); };
}

async function filesDelete(paths) {
  const appData = fs.data?.app || paths.some((p) => /^\/AppData\/[^/]+$/.test(p));
  if (appData || paths.length > 1) {
    const ok = await confirmBox(paths.length > 1 ? `Radera ${paths.length} saker?` : "Radera?",
      appData ? "Det här är data som en app använder. Appen kan sluta fungera tills du ångrar. Allt hamnar i papperskorgen i 30 dagar." : "Allt hamnar i papperskorgen i 30 dagar och kan ångras.", "Radera", true);
    if (!ok) return;
  }
  try {
    const r = await api("/api/files/delete", { method: "POST", body: { paths } });
    paths.forEach((p) => fs.sel.delete(p));
    filesLoad();
    toastAction(paths.length > 1 ? `${paths.length} saker ligger i papperskorgen` : `${paths[0].split("/").pop()} ligger i papperskorgen`, "↶ Ångra", () => undoTrash(r.trash));
  } catch (e) { toast(e.message, "bad"); }
}

function filesUpload(list) {
  if (!list.length) return;
  let panel = $("#fuploads");
  if (!panel) { panel = document.createElement("div"); panel.id = "fuploads"; panel.className = "f-uploads card"; document.body.append(panel); }
  const dir = fs.path;
  const queue = list.map((file) => {
    const row = document.createElement("div"); row.className = "f-up-row";
    row.innerHTML = `<div class="n"></div><div class="progress"><i></i></div><div class="m">Väntar …</div>`;
    $(".n", row).textContent = file.name; panel.append(row);
    return { file, row };
  });
  const send = (q, overwrite) => new Promise((resolve) => {
    const x = new XMLHttpRequest();
    x.open("PUT", `/api/files/upload?dir=${encodeURIComponent(dir)}&name=${encodeURIComponent(q.file.name)}${overwrite ? "&overwrite=1" : ""}`);
    x.setRequestHeader("x-wizzos", "1");
    x.upload.onprogress = (e) => { if (e.lengthComputable) { const pc = Math.round(e.loaded / e.total * 100); $("i", q.row).style.width = pc + "%"; $(".m", q.row).textContent = `${pc} % av ${fmtBytes(e.total)}`; } };
    x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch { /* */ } resolve({ status: x.status, data: d }); };
    x.onerror = () => resolve({ status: 0, data: { detail: "Nätverksfel" } });
    x.send(q.file);
  });
  (async () => {
    for (const q of queue) {
      $(".m", q.row).textContent = "Laddar upp …";
      let r = await send(q, false);
      if (r.status === 409) {
        const ok = await confirmBox("Filen finns redan", `${q.file.name} finns redan i ${dir}. Vill du ersätta den? Den gamla hamnar i papperskorgen.`, "Ersätt");
        if (ok) r = await send(q, true); else { $(".m", q.row).textContent = "Hoppade över"; q.row.classList.add("skip"); continue; }
      }
      if (r.status === 200) { q.row.classList.add("done"); $(".m", q.row).textContent = `Klar · ${fmtBytes(q.file.size)}`; }
      else { q.row.classList.add("fail"); $(".m", q.row).textContent = r.data.detail || `Fel ${r.status}`; }
    }
    if (location.hash.startsWith("#/files") && fs.path === dir) filesLoad();
    const ok = queue.filter((q) => q.row.classList.contains("done")).length;
    toast(`${ok} av ${queue.length} filer uppladdade`, ok === queue.length ? "good" : "bad");
    setTimeout(() => { queue.forEach((q) => q.row.remove()); if (!panel.children.length) panel.remove(); }, 6000);
  })();
}

async function showTrash() {
  const draw = async () => {
    const t = await api("/api/trash");
    const body = openModal(`<h3>🗑️ Papperskorgen</h3><p>Raderat ligger kvar i ${t.days} dagar. Därefter försvinner det av sig självt.</p>
      <div class="card list">${t.items.map((x) => `<div class="row"><div><div class="n">${x.dir ? "📁" : fileIcon(x)} ${esc(x.name)}
        ${x.reason === "edited" ? '<span class="badge">före ändring</span>' : x.reason === "replaced" ? '<span class="badge">ersatt</span>' : ""}</div>
        <div class="m">${esc(x.orig)} · ${fmtBytes(x.size || 0)} · ${esc(timeAgo(x.time))}</div></div>
        <div class="acts"><button class="btn" data-r="${esc(x.id)}">↶ Lägg tillbaka</button><button class="icon-btn" data-x="${esc(x.id)}" title="Radera för gott" aria-label="Radera för gott"><svg><use href="#i-x"/></svg></button></div></div>`).join("") || `<div class="empty">Papperskorgen är tom.</div>`}</div>
      <div class="actions">${t.items.length ? '<button class="btn danger" id="tempty">Töm papperskorgen</button>' : ""}<button class="btn" data-close>Stäng</button></div>`);
    $$("[data-r]", body).forEach((b) => b.onclick = async () => {
      try { const r = await api(`/api/trash/${encodeURIComponent(b.dataset.r)}/restore`, { method: "POST" }); toast(`Tillbaka i ${r.path}`, "good"); draw(); filesLoad(); } catch (e) { toast(e.message, "bad"); }
    });
    $$("[data-x]", body).forEach((b) => b.onclick = async () => {
      const ok = await confirmBox("Radera för gott?", "Det går inte att ångra.", "Radera för gott", true); if (!ok) return draw();
      try { await api(`/api/trash/${encodeURIComponent(b.dataset.x)}`, { method: "DELETE" }); } catch (e) { toast(e.message, "bad"); } draw(); filesLoad();
    });
    $("#tempty", body)?.addEventListener("click", async () => {
      const ok = await confirmBox("Töm papperskorgen?", `${t.items.length} saker raderas för gott. Det går inte att ångra.`, "Töm", true); if (!ok) return draw();
      try { await api("/api/trash", { method: "DELETE" }); toast("Papperskorgen är tömd", "good"); } catch (e) { toast(e.message, "bad"); } draw(); filesLoad();
    });
  };
  try { await draw(); } catch (e) { toast(e.message, "bad"); }
}

/* ---------------- Appar: öppna, meny, detaljer ---------------- */

function appUrl(a) {
  if (!a.port) return "";
  const scheme = a.scheme === "https" ? "https" : "http";
  let idx = a.index || "/"; if (!idx.startsWith("/")) idx = "/" + idx;
  return `${scheme}://${location.hostname}:${a.port}${idx}`;
}
function openApp(a) {
  if (a.asleep) {
    api(`/api/apps/${a.name}/wake`, { method: "POST" }).catch(() => {});
    a.asleep = false;
    if (!a.url) { toast(`💤 ${a.title} vaknar – öppnas om några sekunder …`); setTimeout(() => openApp(a), 7000); return; }
  }
  if (a.url) { window.open(a.url, "_blank", "noopener"); return; }
  const u = appUrl(a);
  if (!u) { toast("Appen har inget webbgränssnitt."); return; }
  if (a.scheme === "https") toast("Varnar webbläsaren för certifikatet? Välj Avancerat → Fortsätt. Appen använder ett eget certifikat.");
  window.open(u, "_blank", "noopener");
}

function sleepMenu(anchor, a, reload) {
  const set = async (m) => {
    try {
      await api(`/api/apps/${a.name}/sleep`, { method: "POST", body: { minutes: m } });
      toast(m ? `${a.title} somnar efter ${m >= 60 ? m / 60 + " h" : m + " min"} utan användning och vaknar när någon öppnar den` : `${a.title} är alltid vaken`, "good");
      reload();
    } catch (e) { toast(e.message, "bad"); }
  };
  openMenu(anchor, [
    { label: "Sov efter 30 min utan användning", run: () => set(30) },
    { label: "Sov efter 1 timme", run: () => set(60) },
    { label: "Sov efter 3 timmar", run: () => set(180) },
    { label: "Alltid vaken", run: () => set(0) },
  ]);
}

function appMenu(anchor, a, reload) {
  const act = async (what) => {
    try {
      const r = await api(`/api/apps/${a.name}/${what}`, { method: "POST" });
      if (r.job) watchJob(r.job, { title: `Uppdaterar ${a.title}`, onDone: reload });
      else { toast(`${a.title}: ${{ start: "startad", stop: "stoppad", restart: "omstartad" }[what]}`, "good"); reload(); }
    } catch (e) { toast(e.message, "bad"); }
  };
  openMenu(anchor, [
    { label: "Öppna", run: () => openApp(a) },
    a.asleep ? { label: "⏰ Väck nu", run: async () => { try { await api(`/api/apps/${a.name}/wake`, { method: "POST" }); toast(`${a.title} vaknar …`, "good"); setTimeout(reload, 6000); } catch (e) { toast(e.message, "bad"); } } }
      : a.status === "running" ? { label: "Stoppa", run: () => act("stop") } : { label: "Starta", run: () => act("start") },
    { label: "Starta om", run: () => act("restart") },
    { label: "Uppdatera (säkert)", run: () => act("update") },
    { label: a.no_auto_update ? "🌙 Uppdatera automatiskt igen" : "🌙 Uppdatera inte automatiskt", run: async () => {
      try { await api(`/api/apps/${a.name}/autoupdate`, { method: "POST", body: { on: !!a.no_auto_update } }); toast(a.no_auto_update ? `${a.title} uppdateras automatiskt igen` : `${a.title} hoppas över på natten`, "good"); reload(); } catch (e) { toast(e.message, "bad"); }
    } },
    ...(a.trial ? [] : [{ label: a.sleep_after ? `💤 Sover efter ${a.sleep_after >= 60 ? a.sleep_after / 60 + " h" : a.sleep_after + " min"} utan användning` : "💤 Låt appen sova när den inte används", run: () => sleepMenu(anchor, a, reload) }]),
    "-",
    ...(a.url ? [{ label: "📱 Öppna i mobilen", run: () => phoneModal(a.url, a.title, a.icon) }] : []),
    ...(a.store_key === "wizzos/searxng" ? [{ label: "🔍 Gör till standardsökmotor", run: () => searchEngineModal(a) }] : []),
    { label: "↶ Ångra och historik", run: () => showHistory(a, reload) },
    ...(a.url ? [{ label: "Byt adress", run: () => changeAddress(a, reload) }] : []),
    { label: "Visa logg", run: () => showLogs(`/api/apps/${a.name}/logs`, a.title, a.name) },
    { label: "Inställningar (avancerat)", run: () => editCompose(a, reload) },
    "-",
    ...(a.trial ? [
      { label: "♥ Behåll appen", run: () => trialAct(a, "keep", reload) },
      { label: "Förläng provet 1 timme", run: () => trialAct(a, "extend", reload) },
      { label: "Avsluta provet nu (tas bort spårlöst)", danger: true, run: () => trialAct(a, "end", reload) },
    ] : [{ label: "Avinstallera", danger: true, run: async () => {
      const r = await confirmBox(`Avinstallera ${a.title}?`, "Appen stoppas och tas bort. Du kan få tillbaka den under \"Nyligen borttagna\" i 30 dagar.", "Avinstallera", true,
        `<label class="radio"><input type="checkbox" id="extra-check"><div><b>Radera även appens data</b><span>/DATA/AppData/${esc(a.name)} – en kopia sparas i 30 dagar, sedan är den borta för gott.</span></div></label>`);
      if (!r) return;
      const res = await api(`/api/apps/${a.name}?data=${r.checked ? "true" : "false"}`, { method: "DELETE" });
      watchJob(res.job, { title: `Tar bort ${a.title}`, onDone: () => { storeState.data = null; reload(); } });
    } }]),
  ]);
}

/* ---------------- Startsidans klocka, läge och kom igång ---------------- */

const SV_WEEKDAY_FMT = { weekday: "long", day: "numeric", month: "long" };
function isoWeek(d) { const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())); const day = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - day); const y = new Date(Date.UTC(t.getUTCFullYear(), 0, 1)); return Math.ceil(((t - y) / 86400000 + 1) / 7); }
function tickClock() {
  const n = new Date();
  const hm = n.toLocaleTimeString(loc(), { hour: "2-digit", minute: "2-digit" });
  const date = n.toLocaleDateString(loc(), SV_WEEKDAY_FMT);
  const t = $("#clk-t"); if (t) { t.textContent = hm; $("#clk-s").textContent = String(n.getSeconds()).padStart(2, "0"); $("#clk-d").textContent = `${date[0].toUpperCase()}${date.slice(1)} · vecka ${isoWeek(n)}`; }
  const st = $("#sc-t"); if (st) { st.textContent = hm; $("#sc-d").textContent = n.toLocaleDateString(loc(), { weekday: "short", day: "numeric", month: "short" }); }
  if (t && n.getSeconds() === 0) $("#greet").textContent = greeting();
}

function drawMood(hs) {
  const el = $("#mood"), orb = $("#orb"); if (!el || !hs.sys) return;
  const s = hs.sys, apps = (hs.apps || []).filter((a) => !a.trial);
  const down = apps.filter((a) => a.status !== "running" && !a.user_stopped);
  const probs = s.problems || [];
  const b = s.backup || {};
  let mood = "good", text;
  if (!s.docker.ok) { mood = "bad"; text = "Docker svarar inte – apparna kan inte köras just nu."; }
  else if (down.length) { mood = "bad"; text = `${down.map((a) => a.title).join(", ")} ${down.length === 1 ? "har" : "har"} stannat. Tryck på appen för att se varför.`; }
  else if (probs.length) { mood = "warn"; text = `${probs.length === 1 ? "En sak" : probs.length + " saker"} att titta på – se nedan.`; }
  else if (!apps.length) { mood = "calm"; text = "Allt är redo. Lägg till din första app – eller prova en utan risk."; }
  else {
    const parts = [`${apps.length === 1 ? "Din app" : `Alla ${apps.length} appar`} är igång`];
    if (b.last_test && b.last_test.ok) parts.push(`backupen provades ${timeAgo(b.last_test.time)} och fungerade`);
    else if (b.last_run && b.last_run.ok) parts.push(`backup gjord ${timeAgo(b.last_run.time)}`);
    else if (!b.configured) { mood = "warn"; parts.push("men backup är inte påslagen än"); }
    text = parts.join(", ") + ". Allt är lugnt.";
    if (mood === "warn") text = text.replace(" Allt är lugnt.", "");
  }
  el.textContent = text;
  orb.className = `orb ${mood}`;
}

const doneKey = (k) => `wz-done-${k}`;
function markDone(k) { try { localStorage.setItem(doneKey(k), "1"); } catch { /* */ } }
function isDone(k) { try { return localStorage.getItem(doneKey(k)) === "1"; } catch { return false; } }

async function drawOnboard(hs) {
  const box = $("#onboard"); if (!box || !hs.sys || !hs.apps) return;
  if (isDone("onboard-hidden")) { box.innerHTML = ""; return; }
  let al = { enabled: false };
  try { al = await api("/api/alerts"); } catch { /* */ }
  const steps = [
    { k: "app", t: "Installera din första app", d: "Eller tryck 🧪 Prova – den försvinner spårlöst efteråt.", href: "#/store", done: hs.apps.length > 0 },
    { k: "backup", t: "Slå på backup", d: "Varje natt, krypterat – och WizzOS provar att återställa den.", href: "#/backup", done: !!(hs.sys.backup && hs.sys.backup.configured) },
    { k: "alerts", t: "Få larm i mobilen", d: "En notis om något går fel – och en när det är löst.", href: "#/settings", done: !!al.enabled },
    { k: "phone", t: "Lägg WizzOS på mobilen", d: "Skanna en QR-kod och lägg den på hemskärmen.", run: () => $("#qa-phone").click(), done: isDone("phone") },
  ];
  const n = steps.filter((x) => x.done).length;
  if (n === steps.length) { box.innerHTML = ""; return; }
  const sig = steps.map((x) => x.done ? 1 : 0).join("");
  if (box._sig === sig) return; // inget ändrat – rita inte om (undviker flimmer)
  box._sig = sig;
  box.innerHTML = `<div class="card onboard fade-in">
    <div class="ob-head"><div><h2>Kom igång</h2><p class="hint">Fyra små steg så är din server trygg och klar. Stegen bockas av av sig själva när du gjort dem.</p></div>
      <div class="ob-prog"><span>${Math.round(n / steps.length * 100)} % klart</span><div class="ob-bar"><i style="width:${n / steps.length * 100}%"></i></div></div>
      <button class="btn" id="ob-x" title="Dölj Kom igång för gott">Dölj</button></div>
    <div class="ob-steps">${steps.map((x, i) => `<${x.href ? `a href="${x.href}"` : "button type=\"button\""} class="ob-step ${x.done ? "done" : ""}" data-i="${i}">
      <span class="ob-n">${x.done ? "✓" : i + 1}</span><div><b>${esc(x.t)}</b><span>${esc(x.d)}</span></div></${x.href ? "a" : "button"}>`).join("")}</div></div>`;
  $$(".ob-step", box).forEach((b) => { const st = steps[+b.dataset.i]; if (st.run) b.onclick = st.run; });
  $("#ob-x", box).onclick = () => { markDone("onboard-hidden"); box.innerHTML = ""; box._sig = "hidden"; };
}

/* ---------------- Serverkortet ---------------- */

const loadImg = (src) => new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => res(null); i.src = src; });
const MONTHS = ["jan", "feb", "mars", "april", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"];
const svDate = (t) => { const d = new Date(t * 1000); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };

function roundRect(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }

async function drawCard(canvas, d, opt) {
  const W = 1200, H = 630, ctx = canvas.getContext("2d");
  canvas.width = W; canvas.height = H;
  const F = 'system-ui, -apple-system, "Segoe UI", Roboto, Inter, sans-serif';
  // Bakgrund: djup natt med två ljusmoln och ett fint punktmönster
  ctx.fillStyle = "#0b0d18"; ctx.fillRect(0, 0, W, H);
  const blob = (x, y, r, c) => { const g = ctx.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, c); g.addColorStop(1, "rgba(0,0,0,0)"); ctx.fillStyle = g; ctx.fillRect(0, 0, W, H); };
  blob(160, 40, 620, "rgba(139,123,255,.55)"); blob(1120, 640, 640, "rgba(47,213,200,.42)"); blob(820, 120, 300, "rgba(255,122,198,.16)");
  ctx.fillStyle = "rgba(255,255,255,.05)";
  for (let x = 24; x < W; x += 28) for (let y = 24; y < H; y += 28) ctx.fillRect(x, y, 2, 2);
  // Ram
  roundRect(ctx, 14, 14, W - 28, H - 28, 30); ctx.strokeStyle = "rgba(255,255,255,.10)"; ctx.lineWidth = 2; ctx.stroke();
  // Logga + namn
  const logo = await loadImg("/static/logo-192.png");
  if (logo) ctx.drawImage(logo, 56, 50, 44, 44);
  ctx.fillStyle = "#fff"; ctx.font = `800 26px ${F}`; ctx.textBaseline = "middle"; ctx.fillText("WizzOS", 112, 73);
  ctx.fillStyle = "rgba(238,240,251,.55)"; ctx.font = `600 20px ${F}`; ctx.textAlign = "right";
  ctx.fillText(svDate(d.date) + " " + new Date(d.date * 1000).getFullYear(), W - 56, 73); ctx.textAlign = "left";
  // Rubrik
  const title = opt.showName ? d.name : "Min egen server";
  ctx.font = `800 64px ${F}`; ctx.textBaseline = "alphabetic";
  const tg = ctx.createLinearGradient(56, 0, 56 + Math.min(ctx.measureText(title).width, 1000), 0);
  tg.addColorStop(0, "#ffffff"); tg.addColorStop(1, "#c9c2ff");
  ctx.fillStyle = tg; ctx.fillText(title.length > 26 ? title.slice(0, 25) + "…" : title, 56, 178);
  const tb = (b) => b >= 1e12 ? `${(b / 1e12).toFixed(1).replace(".", ",")} TB` : `${Math.round(b / 1e9)} GB`;
  ctx.fillStyle = "rgba(238,240,251,.72)"; ctx.font = `500 26px ${F}`;
  ctx.fillText([`${d.apps.length} ${d.apps.length === 1 ? "app" : "appar"}`, `uppe ${d.uptime_days} ${d.uptime_days === 1 ? "dag" : "dagar"}`, tb(d.disk_total) + " lagring"].join("  ·  "), 58, 222);
  // Lägesorben till höger (WizzOS kännetecken) när det finns plats
  const show = d.apps.slice(0, opt.showNames ? 9 : 12);
  if (show.length <= 6) {
    const ox = 1010, oy = 270, r = 104;
    [[r + 46, .12], [r + 24, .22]].forEach(([rr, a]) => { ctx.beginPath(); ctx.arc(ox, oy, rr, 0, Math.PI * 2); ctx.strokeStyle = `rgba(61,220,151,${a})`; ctx.lineWidth = 2; ctx.stroke(); });
    const glow = ctx.createRadialGradient(ox, oy, r * .4, ox, oy, r * 2.2); glow.addColorStop(0, "rgba(61,220,151,.35)"); glow.addColorStop(1, "rgba(61,220,151,0)");
    ctx.fillStyle = glow; ctx.fillRect(ox - r * 2.2, oy - r * 2.2, r * 4.4, r * 4.4);
    const og = ctx.createRadialGradient(ox, oy + r * .1, 0, ox, oy, r); og.addColorStop(0, "#3ddc97"); og.addColorStop(1, "#2fd5c8");
    ctx.beginPath(); ctx.arc(ox, oy, r, 0, Math.PI * 2); ctx.fillStyle = og; ctx.fill();
    const hl = ctx.createRadialGradient(ox - r * .36, oy - r * .44, 0, ox - r * .36, oy - r * .44, r * .5); hl.addColorStop(0, "rgba(255,255,255,.85)"); hl.addColorStop(1, "rgba(255,255,255,0)");
    ctx.beginPath(); ctx.arc(ox, oy, r, 0, Math.PI * 2); ctx.fillStyle = hl; ctx.fill();
  }
  // Appikoner
  const size = 76, gap = opt.showNames ? 44 : 22, top = 268;
  const imgs = await Promise.all(show.map((a) => loadImg(`/api/icon/${encodeURIComponent(a.name)}`)));
  show.forEach((a, i) => {
    const x = 58 + i * (size + gap);
    ctx.save(); roundRect(ctx, x, top, size, size, 20); ctx.fillStyle = "rgba(255,255,255,.09)"; ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,.12)"; ctx.lineWidth = 1.5; ctx.stroke(); ctx.clip();
    if (imgs[i]) ctx.drawImage(imgs[i], x + 10, top + 10, size - 20, size - 20);
    else { const g = ctx.createLinearGradient(x, top, x + size, top + size); g.addColorStop(0, "#8b7bff"); g.addColorStop(1, "#2fd5c8"); ctx.fillStyle = g; ctx.fillRect(x, top, size, size);
      ctx.fillStyle = "#fff"; ctx.font = `800 34px ${F}`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText((a.title || "?")[0].toUpperCase(), x + size / 2, top + size / 2 + 2); ctx.textAlign = "left"; ctx.textBaseline = "alphabetic"; }
    ctx.restore();
    if (a.running) { ctx.beginPath(); ctx.arc(x + size - 6, top + 6, 7, 0, Math.PI * 2); ctx.fillStyle = "#3ddc97"; ctx.fill(); ctx.strokeStyle = "#0b0d18"; ctx.lineWidth = 3; ctx.stroke(); }
    if (opt.showNames) { ctx.fillStyle = "rgba(238,240,251,.8)"; ctx.font = `600 15px ${F}`; ctx.textAlign = "center";
      const t = a.title.length > 13 ? a.title.slice(0, 12) + "…" : a.title; ctx.fillText(t, x + size / 2, top + size + 24); ctx.textAlign = "left"; }
  });
  if (d.apps.length > show.length) {
    const x = 58 + show.length * (size + gap);
    roundRect(ctx, x, top, size, size, 20); ctx.fillStyle = "rgba(255,255,255,.06)"; ctx.fill();
    ctx.fillStyle = "rgba(238,240,251,.8)"; ctx.font = `700 24px ${F}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(`+${d.apps.length - show.length}`, x + size / 2, top + size / 2); ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  }
  // Märken
  const badges = [];
  if (d.backup.proven) badges.push(["🛡️  Backup bevisad " + svDate(d.backup.proven), "#3ddc97"]);
  else if (d.backup.configured) badges.push(["🛡️  Backup varje natt", "#3ddc97"]);
  if (d.undo_points) badges.push([`↶  ${d.undo_points} ångra-punkter`, "#8b7bff"]);
  if (d.nestward && d.nestward.score != null) badges.push([`🏠  Hemmet ${d.nestward.score}/100`, d.nestward.score >= 85 ? "#3ddc97" : "#ffc857"]);
  badges.push(["🔒  Privat – bara jag", "#2fd5c8"]);
  let bx = 58; const by = 452;
  ctx.font = `700 21px ${F}`;
  badges.forEach(([t, c]) => {
    const w = ctx.measureText(t).width + 40;
    if (bx + w > W - 56) return;
    roundRect(ctx, bx, by, w, 50, 25); ctx.fillStyle = c + "22"; ctx.fill(); ctx.strokeStyle = c + "88"; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.textBaseline = "middle"; ctx.fillText(t, bx + 20, by + 26); ctx.textBaseline = "alphabetic";
    bx += w + 12;
  });
  // Fot
  ctx.fillStyle = "rgba(238,240,251,.5)"; ctx.font = `500 19px ${F}`;
  ctx.fillText("Appar med ett klick · ångra-knapp på allt · backup som testas varje natt", 58, 566);
  const fg = ctx.createLinearGradient(W - 340, 0, W - 56, 0); fg.addColorStop(0, "#8b7bff"); fg.addColorStop(1, "#2fd5c8");
  ctx.fillStyle = fg; ctx.font = `800 22px ${F}`; ctx.textAlign = "right"; ctx.fillText("byggd med WizzOS", W - 56, 566); ctx.textAlign = "left";
}

async function shareCard() {
  let d;
  try { d = await api("/api/card"); } catch (e) { toast(e.message, "bad"); return; }
  const opt = { showName: false, showNames: true };
  const body = openModal(`<h3>🪪 Dela din server</h3>
    <p class="hint">En bild att visa upp – i forum, på Reddit eller för en vän. Den visar inga adresser, IP eller nycklar.</p>
    <div class="card-wrap"><canvas id="sc" aria-label="Serverkort"></canvas></div>
    <div class="card-opts"><label class="radio"><input type="checkbox" id="sco1"><div><b>Visa servernamnet</b><span>${esc(d.name)}</span></div></label>
      <label class="radio"><input type="checkbox" id="sco2" checked><div><b>Visa appnamnen</b><span>Annars bara ikonerna</span></div></label></div>
    <div class="actions"><button class="btn" id="sccp">Kopiera bild</button>${navigator.share ? '<button class="btn" id="scsh">Dela …</button>' : ""}<button class="btn primary" id="scdl">⬇ Spara bild</button></div>`);
  const cv = $("#sc", body);
  const redraw = () => drawCard(cv, d, opt);
  await redraw();
  $("#sco1", body).onchange = (e) => { opt.showName = e.target.checked; redraw(); };
  $("#sco2", body).onchange = (e) => { opt.showNames = e.target.checked; redraw(); };
  const blob = () => new Promise((r) => cv.toBlob(r, "image/png"));
  $("#scdl", body).onclick = async () => { const a = document.createElement("a"); a.href = URL.createObjectURL(await blob()); a.download = "min-wizzos-server.png"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 3000); };
  $("#sccp", body).onclick = async () => { try { await navigator.clipboard.write([new ClipboardItem({ "image/png": await blob() })]); toast("Bilden är kopierad", "good"); } catch { toast("Webbläsaren tillåter inte kopiering – spara bilden i stället", "bad"); } };
  $("#scsh", body)?.addEventListener("click", async () => {
    try { const f = new File([await blob()], "min-wizzos-server.png", { type: "image/png" }); await navigator.share({ files: [f], title: "Min server", text: "Min egen server med WizzOS" }); } catch { /* avbrutet */ }
  });
}

/* ---------------- Nattliga uppdateringar ---------------- */

function updSummary(r) {
  const p = [];
  if (r.updated.length) p.push("Uppdaterade: " + r.updated.map((u) => u.title + (u.to ? ` (${u.to})` : "")).join(", "));
  if (r.rolled_back.length) p.push("Backades: " + r.rolled_back.map((x) => x.title).join(", "));
  if (r.available.length) p.push("Ny version finns: " + r.available.join(", "));
  if (r.failed.length) p.push("Kunde inte kontrolleras: " + r.failed.join(", "));
  if (!p.length) p.push(`Allt var redan senaste version (${r.unchanged} appar)`);
  return p.join(" · ");
}

/* ---------------- Veckobrev ---------------- */

function showLetter(l, preview = false) {
  openModal(`<div class="letter ${l.mood === "good" ? "good" : "warn"}">
    <div class="letter-head"><div class="letter-stamp">💌</div><div><div class="hint">${preview ? "Förhandsvisning · " : ""}${esc(fmtDate(l.time))}</div><h3>${esc(l.title)}</h3></div></div>
    <ul>${l.lines.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    ${l.tip ? `<div class="letter-tip">💡 ${esc(l.tip)}</div>` : ""}
    <div class="letter-sign">Hälsningar, din server</div></div>
    <div class="actions"><button class="btn" data-close>Stäng</button></div>`);
}

/* ---------------- Till mobilen ---------------- */

function phoneModal(url, title, iconUrl) {
  const isPrivate = /^(100\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(new URL(url).hostname) || /tailscale|ts\.net/.test(url)
    || (window.WIZZ_PRIVATE_DOMAIN && new URL(url).hostname.endsWith(window.WIZZ_PRIVATE_DOMAIN));
  openModal(`<div class="phone">
    <div class="phone-qr"><img src="/api/qr?text=${encodeURIComponent(url)}" alt="QR-kod till ${esc(title)}" width="220" height="220">
      <div class="phone-ico">${icon(iconUrl, title)}</div></div>
    <div><h3>📱 ${esc(title)} i mobilen</h3>
      <p>Rikta mobilens kamera mot koden och tryck på länken som dyker upp.</p>
      <p class="mono-url">${esc(url)}</p>
      ${isPrivate ? `<p class="hint">🔒 Adressen är privat. Mobilen behöver vara på samma nätverk, eller ha <b>Tailscale</b> påslaget.</p>` : ""}
      <details open><summary><b>Lägg den på hemskärmen</b> – då känns den som en vanlig app</summary>
        <div class="howto"><div><b> iPhone</b><span>Öppna i Safari → tryck på dela-knappen <b>⬆︎</b> → <b>Lägg till på hemskärmen</b>.</span></div>
        <div><b>🤖 Android</b><span>Öppna i Chrome → tryck på <b>⋮</b> → <b>Lägg till på startskärmen</b> (eller <b>Installera app</b>).</span></div></div></details>
      <div class="actions" style="justify-content:flex-start"><button class="btn" id="phcp">Kopiera adressen</button></div>
    </div></div>`);
  $("#phcp").onclick = async () => { try { await navigator.clipboard.writeText(url); toast("Adressen är kopierad", "good"); } catch { toast(url); } };
}

/* ---------------- Flytta servern ---------------- */

function moveOut() {
  const body = openModal(`<h3>📦 Flytta till en ny server</h3>
    <p>Byter du dator eller VPS? WizzOS gör en färsk backup och ger dig en <b>flyttkod</b>. På den nya servern installerar du WizzOS,
      klistrar in koden – och alla appar kommer tillbaka med sin data, sina adresser och samma programversioner.</p>
    <div class="steps-mini"><span>1</span> Välj ett flyttlösenord <span>2</span> Spara flyttkoden <span>3</span> Klistra in den på nya servern</div>
    <label for="mvp1">Flyttlösenord (minst 10 tecken)</label><input id="mvp1" type="password" autocomplete="new-password">
    <label for="mvp2">Samma lösenord igen</label><input id="mvp2" type="password" autocomplete="new-password">
    <label class="radio" style="margin-top:10px"><input type="checkbox" id="mvdirect"><div><b>Direktflytt – den nya servern står i samma nätverk</b>
      <span>Den nya servern hämtar allt rakt härifrån över nätverket – mycket snabbare för stora mappar som foton, och utan molnet. Öppet i 6 timmar, bara för datorer i ditt eget nät.</span></div></label>
    <div id="mvurlw" hidden><label for="mvurl">Den här serverns adress i nätverket (valfritt)</label><input id="mvurl" placeholder="http://192.168.1.20:8899"><p class="hint">Lämna tomt så väljer WizzOS själv.</p></div>
    <div id="mvopen"></div>
    <p class="hint">Koden innehåller nycklarna till backupen och är krypterad med lösenordet. Utan lösenordet är den oläslig. Den gamla servern påverkas inte – apparna pausas bara ett ögonblick medan de kopieras.</p>
    <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="mvgo">Skapa flyttkod</button></div>`);
  $("#mvdirect", body).onchange = (e) => { $("#mvurlw", body).hidden = !e.target.checked; };
  api("/api/move/direct").then((st) => {
    if (!st.open) return;
    $("#mvopen", body).innerHTML = `<p class="hint">🔓 Direktflytt är öppen till ${esc(fmtDate(st.until))}. <button class="btn" id="mvclose">Stäng direktflytten</button></p>`;
    $("#mvclose", body).onclick = async () => { await api("/api/move/direct/close", { method: "POST" }); $("#mvopen", body).innerHTML = ""; toast("Direktflytten är stängd", "good"); };
  }).catch(() => {});
  $("#mvgo", body).onclick = async () => {
    const a = $("#mvp1", body).value, b = $("#mvp2", body).value;
    if (a !== b) { toast("Lösenorden är inte lika", "bad"); return; }
    try {
      const r = await api("/api/move/export", { method: "POST", body: { passphrase: a, direct: $("#mvdirect", body).checked, url: $("#mvurl", body).value.trim() } });
      watchJob(r.job, { title: "Förbereder flytt", onDone: (j) => { if (j.state === "done" && j.result?.code) setTimeout(() => showMoveCode(j.result.code), 600); } });
    } catch (e) { toast(e.message, "bad"); }
  };
}

function showMoveCode(code) {
  const body = openModal(`<h3>✅ Flyttkoden är klar</h3>
    <p>Spara den – t.ex. i din lösenordshanterare – tillsammans med flyttlösenordet. På den nya servern: installera WizzOS, gå till <b>Backup</b> och tryck på <b>Flyttar du hit från en annan WizzOS?</b></p>
    <textarea id="mvc" readonly style="min-height:120px;word-break:break-all"></textarea>
    <div class="actions"><button class="btn" id="mvdl">⬇ Spara som fil</button><button class="btn primary" id="mvcp">Kopiera</button></div>`);
  const ta = $("#mvc", body); ta.value = code;
  $("#mvcp", body).onclick = async () => { try { await navigator.clipboard.writeText(code); toast("Kopierad", "good"); } catch { ta.select(); document.execCommand("copy"); toast("Kopierad", "good"); } };
  $("#mvdl", body).onclick = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([`WizzOS flyttkod – ${new Date().toLocaleString(loc())}\nBehåll flyttlösenordet separat.\n\n${code}\n`], { type: "text/plain" }));
    a.download = `wizzos-flyttkod-${new Date().toISOString().slice(0, 10)}.txt`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
}

function moveIn() {
  const body = openModal(`<h3>📦 Flytta hit en WizzOS</h3>
    <p>Klistra in flyttkoden från den gamla servern och skriv flyttlösenordet. Du får se vad som kommer att hända innan något görs.</p>
    <label for="mic">Flyttkod</label><textarea id="mic" style="min-height:100px" placeholder="WZMOVE1.…" spellcheck="false"></textarea>
    <label for="mip">Flyttlösenord</label><input id="mip" type="password" autocomplete="off">
    <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="migo">Fortsätt</button></div>`);
  $("#migo", body).onclick = async () => {
    const code = $("#mic", body).value, passphrase = $("#mip", body).value;
    const go = $("#migo", body); go.disabled = true; go.textContent = "Kollar den här servern …";
    let pv;
    try { pv = await api("/api/move/preview", { method: "POST", body: { code, passphrase } }); } catch (e) { toast(e.message, "bad"); go.disabled = false; go.textContent = "Fortsätt"; return; }
    const dot = { ok: "🟢", warn: "🟡", bad: "🔴" };
    const head = { ok: "Den här servern klarar allt 🎉", warn: "Det går, men kolla det gula först", bad: "Något hindrar flytten – se det röda" }[pv.level];
    body.innerHTML = `<h3>🩺 Hälsokoll: ${esc(head)}</h3>
      <p class="hint">Från <b>${esc(pv.from || "okänd server")}</b> (kod skapad ${pv.made ? esc(fmtDate(pv.made)) : "?"}). Inget har ändrats än.</p>
      <div class="card list">${pv.checks.map((c) => `<div class="row"><div><div class="n">${dot[c.level]} ${esc(c.title)}</div><div class="m">${esc(c.msg)}</div></div></div>`).join("")}</div>
      <h4 style="margin:14px 0 6px">Appar (${pv.apps.length})</h4>
      <div class="card list">${pv.apps.map((x) => `<div class="row"><div><div class="n">${dot[x.level]} ${esc(x.title)}${x.asleep ? " 💤" : ""}</div>
        <div class="m">${x.mem_mb ? `${x.mem_mb} MB minne · ` : ""}${fmtBytes(x.data_bytes || 0)}${x.notes.length ? " – " + x.notes.map(esc).join(" ") : ""}</div></div></div>`).join("") || `<div class="empty">Inga appar.</div>`}</div>
      ${pv.folders.length ? `<h4 style="margin:14px 0 6px">Mappar</h4><div class="card list">${pv.folders.map((f) => `<div class="row"><div><div class="n">📁 ${esc(f.path)}</div><div class="m">${fmtBytes(f.size)} · ${f.files} filer</div></div></div>`).join("")}</div>` : ""}
      ${pv.direct ? `<label class="radio" style="margin-top:10px"><input type="checkbox" id="midirect" checked><div><b>Hämta direkt från den gamla servern</b><span>Snabbast i samma nätverk. Avmarkera för att använda backupen.</span></div></label>` : ""}
      ${pv.proxy_was_on ? `<p class="hint">Egen adress (${esc(pv.proxy_domain)}) förifylls – du slår på den själv när adresserna ska peka hit.</p>` : ""}
      <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="mirun" ${pv.level === "bad" ? "" : ""}>${pv.level === "bad" ? "Flytta ändå" : "Flytta hit allt"}</button></div>`;
    body.querySelectorAll("[data-close]").forEach((b) => b.onclick = () => closeModal());
    $("#mirun", body).onclick = async () => {
      if (pv.level === "bad" && !(await confirmBox("Flytta ändå?", "Hälsokollen hittade något rött. Det som inte passar kan misslyckas – resten flyttas.", "Flytta ändå"))) return;
      try {
        const r = await api("/api/move/import", { method: "POST", body: { code, passphrase, direct: $("#midirect", body) ? $("#midirect", body).checked : false } });
        closeModal();
        watchJob(r.job, { title: "Flyttar hit servern", onDone: () => { storeState.data = null; } });
      } catch (e) { toast(e.message, "bad"); }
    };
  };
}

/* ---------------- Prova först ---------------- */

function fmtLeft(sec, short = false) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  if (short) return h ? `${h} h ${m} min` : m ? `${m} min` : `${s} s`;
  return h ? `${h} h ${m} min` : m ? `${m} min ${String(s).padStart(2, "0")} s` : `${s} s`;
}
function tickCountdowns() {
  const now = Date.now() / 1000;
  $$("[data-until]").forEach((el) => { el.textContent = fmtLeft(el.dataset.until - now, el.hasAttribute("data-short")); });
}
async function trialAct(a, what, reload) {
  try {
    if (what === "end") {
      const ok = await confirmBox(`Ta bort ${a.title} nu?`, "Appen och allt den skapat tas bort spårlöst. Det går inte att ångra.", "Ta bort", true);
      if (!ok) return;
      const r = await api(`/api/apps/${a.name}/trial/end`, { method: "POST" });
      watchJob(r.job, { title: `Avslutar provet av ${a.title}`, onDone: reload });
      return;
    }
    await api(`/api/apps/${a.name}/trial/${what}`, { method: "POST", body: what === "extend" ? { minutes: 60 } : undefined });
    toast(what === "keep" ? `${a.title} är din nu 🎉` : `${a.title}: en timme till`, "good");
    reload();
  } catch (e) { toast(e.message, "bad"); }
}

function changeAddress(a, reload) {
  const host = new URL(a.url).hostname;
  const domain = host.slice(host.indexOf(".") + 1);
  const body = openModal(`
    <div class="app-hero">${icon(a.icon, a.title)}<div><h3>Adress för ${esc(a.title)}</h3>
      <p>Nu: <b>${esc(host)}</b></p></div></div>
    <label for="sub">Nytt namn</label>
    <div class="addr"><input id="sub" value="${esc(a.sub || "")}" placeholder="${esc(a.address)}" autocomplete="off" spellcheck="false"><span>.${esc(domain)}</span></div>
    <p class="hint">Små bokstäver, siffror och bindestreck. Lämna tomt för automatiskt namn.</p>
    <p class="addr-preview" id="prev"></p>
    <p class="error" id="aerr"></p>
    <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="asave">Spara</button></div>`);
  const inp = $("#sub", body);
  const upd = () => {
    const v = inp.value.trim().toLowerCase();
    const ok = !v || /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(v);
    $("#prev", body).innerHTML = ok ? `Blir: <b>https://${esc(v || a.address)}.${esc(domain)}</b>` : "";
    $("#aerr", body).textContent = ok ? "" : "Bara a–z, 0–9 och bindestreck (inte först eller sist).";
    $("#asave", body).disabled = !ok;
  };
  inp.oninput = upd; upd(); inp.focus(); inp.select();
  inp.onkeydown = (e) => { if (e.key === "Enter" && !$("#asave", body).disabled) $("#asave", body).click(); };
  $("#asave", body).onclick = async () => {
    try {
      const r = await api(`/api/apps/${a.name}/address`, { method: "PUT", body: { sub: inp.value } });
      closeModal(); toast(`${a.title} nås nu på ${new URL(r.url).hostname}`, "good"); reload();
    } catch (e) { $("#aerr", body).textContent = e.message; }
  };
}

/* ---------------- Backup: guide, nyckel, återställning ---------------- */

async function backupSetup(view, c) {
  const T = {
    local: { t: "Mapp eller USB-disk", d: "En mapp under /DATA, t.ex. en extra disk. Enklast – men ligger i samma dator.", i: "💽" },
    sftp: { t: "En annan server", d: "Via SFTP till t.ex. en annan VPS eller hemmaservern. Skyddar om den här servern dör.", i: "🖥️" },
    s3: { t: "Molnlagring", d: "Cloudflare R2, Backblaze B2 eller annan S3-lagring. Billigt och långt bort.", i: "☁️" },
  };
  let type = c.type || "";
  const draw = async () => {
    const pub = type === "sftp" ? (await api("/api/backup/key")).public_key : "";
    view.innerHTML = `
      <div class="head fade-in"><div><h1>Backup</h1><p>Skydda dina appar – och få bevis på att det fungerar.</p></div>
        <button class="btn" id="mvin">📦 Flyttar du hit från en annan WizzOS?</button></div>
      <div class="card bk-hero fade-in"><div class="bk-shield"><svg><use href="#i-shield"/></svg></div><div>
        <h3>Så fungerar det</h3>
        <p>Varje natt sparas alla appar krypterat – bara det som ändrats. Sedan återställs en app på prov i en sandlåda och startas, så du vet att backupen faktiskt går att använda.</p></div></div>
      <h2>Var ska backupen sparas?</h2>
      <div class="bk-choices">${Object.entries(T).map(([k, v]) => `<button class="card bk-choice ${type === k ? "on" : ""}" data-t="${k}"><span class="bk-i">${v.i}</span><b>${v.t}</b><span>${v.d}</span></button>`).join("")}</div>
      ${type ? `<div class="card settings-inline fade-in" style="margin-top:16px">
        ${type === "local" ? `<label for="f-path">Mapp</label><input id="f-path" value="${esc(c.path || "/DATA/Backup")}"><p class="hint">Måste ligga under /DATA. Bäst är en separat disk monterad där, t.ex. /DATA/USB/backup.</p>` : ""}
        ${type === "sftp" ? `<div class="grid2"><div><label for="f-user">Användare</label><input id="f-user" value="${esc(c.sftp_user)}" placeholder="backup"></div>
          <div><label for="f-host">Server</label><input id="f-host" value="${esc(c.sftp_host)}" placeholder="100.x.x.x eller server.se"></div>
          <div><label for="f-port">Port</label><input id="f-port" value="${esc(c.sftp_port || "22")}"></div>
          <div><label for="f-spath">Mapp på servern</label><input id="f-spath" value="${esc(c.sftp_path || "wizzos-backup")}"></div></div>
          <label>WizzOS publika nyckel</label><div class="term" style="height:auto;min-height:0">${esc(pub)}</div>
          <p class="hint">Lägg in raden ovan i <b>~/.ssh/authorized_keys</b> för användaren på den andra servern. Den ger bara inloggning för den här nyckeln – inget lösenord behövs.</p>` : ""}
        ${type === "s3" ? `<div class="grid2"><div><label for="f-end">Adress (endpoint)</label><input id="f-end" value="${esc(c.s3_endpoint)}" placeholder="https://<konto>.r2.cloudflarestorage.com"></div>
          <div><label for="f-bucket">Bucket</label><input id="f-bucket" value="${esc(c.s3_bucket)}" placeholder="wizzos-backup"></div>
          <div><label for="f-key">Nyckel-id (Access Key ID)</label><input id="f-key" value="${esc(c.s3_key)}" autocomplete="off"></div>
          <div><label for="f-sec">Hemlig nyckel</label><input id="f-sec" type="password" autocomplete="off" placeholder="${c.has_s3_secret ? "Sparad" : ""}"></div></div>` : ""}
        <div class="grid2"><div><label for="f-time">Tid varje natt</label><input id="f-time" type="time" value="${esc(c.time || "03:30")}"></div>
        <div><label for="f-cap">Tak (GB, 0 = inget)</label><input id="f-cap" type="number" min="0" step="0.5" value="${esc(c.max_gb || (type === "s3" ? 9 : 0))}"></div></div>
        ${type === "s3" ? `<p class="hint">Cloudflare R2 är gratis upp till 10 GB – taket 9 GB gör att det aldrig kostar något.</p>` : ""}
        <details style="margin-top:12px"><summary>Jag har redan en backup här (t.ex. ny server)</summary>
          <label for="f-exist">Återställningsnyckel</label><input id="f-exist" type="password" autocomplete="off" placeholder="Nyckeln du sparade när backupen skapades">
          <p class="hint">Då ansluter WizzOS till den befintliga backupen, och du kan återställa dina appar härifrån.</p></details>
        <div class="actions"><button class="btn primary" id="f-go">Testa och slå på</button></div></div>` : ""}`;
    $$(".bk-choice").forEach((b) => b.onclick = () => { type = b.dataset.t; draw(); });
    $("#mvin").onclick = moveIn;
    if (!type) return;
    $("#f-go").onclick = async () => {
      const v = (id) => $(id)?.value;
      const body = { type, path: v("#f-path"), sftp_user: v("#f-user"), sftp_host: v("#f-host"), sftp_port: v("#f-port"), sftp_path: v("#f-spath"),
        s3_endpoint: v("#f-end"), s3_bucket: v("#f-bucket"), s3_key: v("#f-key"), s3_secret: v("#f-sec"), time: v("#f-time"), max_gb: v("#f-cap"), existing_password: v("#f-exist") };
      try {
        const r = await api("/api/backup/setup", { method: "POST", body });
        watchJob(r.job, { title: "Ställer in backup", onDone: (j) => { if (j.state === "done") { const usedExisting = !!body.existing_password; route(); if (!usedExisting) setTimeout(keyIntro, 400); } } });
      } catch (e) { toast(e.message, "bad"); }
    };
  };
  draw();
}

function keyIntro() {
  const body = openModal(`<h3>🔑 Spara din återställningsnyckel</h3>
    <p>Backupen är krypterad. För att kunna återställa på en <b>ny</b> server behöver du nyckeln – utan den går backupen inte att läsa, inte ens för dig.</p>
    <p>Spara den i Vaultwarden (eller skriv ut den). Du kan alltid visa den igen under Backup.</p>
    <div class="actions"><button class="btn" data-close>Senare</button><button class="btn primary" id="kshow">Visa nyckeln</button></div>`);
  $("#kshow", body).onclick = revealKey;
}

function revealKey() {
  const body = openModal(`<h3>🔑 Återställningsnyckel</h3><p>Skriv ditt WizzOS-lösenord för att visa nyckeln.</p>
    <input type="password" id="rpw" autocomplete="current-password"><p class="error" id="rerr"></p>
    <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="rgo">Visa</button></div>`);
  const go = async () => {
    try {
      const r = await api("/api/backup/reveal", { method: "POST", body: { password: $("#rpw", body).value } });
      const b2 = openModal(`<h3>🔑 Återställningsnyckel</h3><p>Spara den här på ett säkert ställe, t.ex. i Vaultwarden.</p>
        <div class="term keybox" id="kv"></div>
        <div class="actions"><button class="btn" id="kcopy">Kopiera</button><button class="btn primary" data-close>Klar</button></div>`);
      $("#kv", b2).textContent = r.key;
      $("#kcopy", b2).onclick = async () => { try { await navigator.clipboard.writeText(r.key); toast("Kopierad", "good"); } catch { toast("Markera och kopiera för hand", ""); } };
    } catch (e) { $("#rerr", body).textContent = e.message; }
  };
  $("#rgo", body).onclick = go;
  $("#rpw", body).onkeydown = (e) => { if (e.key === "Enter") go(); };
  $("#rpw", body).focus();
}

async function restoreFromBackup(name, title, reload) {
  openModal(`<div class="empty"><span class="spin" style="display:inline-block"></span><p>Hämtar listan från backupen …</p></div>`);
  let list;
  try { list = await api(`/api/backup/apps/${name}/snapshots`); } catch (e) { closeModal(); toast(e.message, "bad"); return; }
  const body = openModal(`<h3>Återställ ${esc(title)} från backup</h3>
    <p class="hint">Appen blir som den var vid den backupen – inställningar, data och programversion. Nuläget sparas först som en Ångra-punkt.</p>
    <div class="timeline">${list.map((s, i) => `<div class="tl-row"><div class="tl-ico">🛡️</div>
      <div class="tl-main"><b>${esc(fmtDate(Date.parse(s.time) / 1000))}</b>${i === 0 ? ' <span class="badge ok">Senaste</span>' : ""}<div class="m">${esc(timeAgo(Date.parse(s.time) / 1000))} · ${fmtBytes(s.size)} · ${esc(s.id)}</div></div>
      <div class="tl-acts"><button class="btn" data-snap="${esc(s.id)}">Återställ hit</button></div></div>`).join("") || `<div class="empty">Ingen backup av appen än.</div>`}</div>
    <div class="actions"><button class="btn" data-close>Stäng</button></div>`);
  $$("[data-snap]", body).forEach((b) => b.onclick = async () => {
    if (!await confirmBox(`Återställ ${title}?`, `Appen återställs till backupen från ${fmtDate(Date.parse(list.find((s) => s.id === b.dataset.snap).time) / 1000)}.`, "Återställ")) return;
    try { const r = await api(`/api/backup/apps/${name}/restore`, { method: "POST", body: { snapshot: b.dataset.snap } }); watchJob(r.job, { title: `Återställer ${title}`, onDone: reload }); }
    catch (e) { toast(e.message, "bad"); }
  });
}

/* ---------------- Ångra ---------------- */

const REASON_ICON = { manual: "📌", update: "⬆️", settings: "⚙️", uninstall: "🗑️", restore: "↶" };
function timeAgo(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "nyss";
  if (s < 3600) return `för ${Math.floor(s / 60)} min sedan`;
  if (s < 86400) return `för ${Math.floor(s / 3600)} h sedan`;
  const d = Math.floor(s / 86400); return d === 1 ? "i går" : `för ${d} dagar sedan`;
}
const fmtDate = (t) => new Date(t * 1000).toLocaleString(loc(), { dateStyle: "medium", timeStyle: "short" });

async function undoTo(name, sid, title, reload) {
  const ok = await confirmBox(`Återställ ${title}?`, "Appen blir exakt som den var vid den tidpunkten – inställningar, data och version. Nuläget sparas först, så du kan ångra även det här.", "Återställ");
  if (!ok) return;
  try {
    const r = await api(`/api/snapshots/${name}/${sid}/restore`, { method: "POST" });
    watchJob(r.job, { title: `Återställer ${title}`, onDone: () => { storeState.data = null; reload && reload(); route(); } });
  } catch (e) { toast(e.message, "bad"); }
}

async function showHistory(a, reload) {
  const draw = async () => {
    const d = await api(`/api/apps/${a.name}/snapshots`);
    const body = openModal(`
      <div class="app-hero">${icon(a.icon, a.title)}<div><h3>Ångra – ${esc(a.title)}</h3>
        <p>WizzOS sparar en punkt före varje uppdatering, ändring och borttagning. Punkterna sparas i ${d.keep_days} dagar.</p></div></div>
      <div class="timeline">${d.snapshots.length ? d.snapshots.map((s, i) => `
        <div class="tl-row">
          <div class="tl-ico">${REASON_ICON[s.reason] || "•"}</div>
          <div class="tl-main"><b>${esc(d.reasons[s.reason] || s.reason)}</b>${i === 0 ? ' <span class="badge ok">Senaste</span>' : ""}
            <div class="m" title="${esc(fmtDate(s.created))}">${esc(timeAgo(s.created))} · ${esc(fmtDate(s.created))}${s.version ? " · v" + esc(s.version) : ""} · ${fmtBytes(s.size)}${s.data ? "" : " · utan data"}</div>
            ${s.note ? `<div class="m warn">${esc(s.note)}</div>` : ""}</div>
          <div class="tl-acts"><button class="btn" data-restore="${esc(s.id)}">Återställ hit</button>
            <button class="icon-btn" data-del="${esc(s.id)}" title="Ta bort punkten" aria-label="Ta bort punkten"><svg><use href="#i-x"/></svg></button></div>
        </div>`).join("") : `<div class="empty">Inga återställningspunkter än.<br>Den första skapas automatiskt vid nästa uppdatering eller ändring.</div>`}</div>
      <div class="actions"><button class="btn" data-close>Stäng</button><button class="btn primary" id="snapnow">📌 Spara en punkt nu</button></div>`);
    $("#snapnow", body).onclick = async () => {
      try { const r = await api(`/api/apps/${a.name}/snapshots`, { method: "POST" }); watchJob(r.job, { title: `Sparar punkt för ${a.title}`, onDone: () => setTimeout(() => showHistory(a, reload), 600) }); }
      catch (e) { toast(e.message, "bad"); }
    };
    $$("[data-restore]", body).forEach((b) => b.onclick = () => undoTo(a.name, b.dataset.restore, a.title, reload));
    $$("[data-del]", body).forEach((b) => b.onclick = async () => {
      if (!await confirmBox("Ta bort punkten?", "Du kan inte återställa till den här tidpunkten efteråt.", "Ta bort", true)) return showHistory(a, reload);
      await api(`/api/snapshots/${a.name}/${b.dataset.del}`, { method: "DELETE" }); showHistory(a, reload);
    });
  };
  try { await draw(); } catch (e) { toast(e.message, "bad"); }
}

function containerMenu(anchor, c, reload) {
  const act = async (what) => { try { await api(`/api/containers/${c.id}/${what}`, { method: "POST" }); toast(`${c.name}: klart`, "good"); reload(); } catch (e) { toast(e.message, "bad"); } };
  openMenu(anchor, [
    c.state === "running" ? { label: "Stoppa", run: () => act("stop") } : { label: "Starta", run: () => act("start") },
    { label: "Starta om", run: () => act("restart") },
    { label: "Visa logg", run: () => showLogs(`/api/containers/${c.id}/logs`, c.name) },
  ]);
}

async function showLogs(url, title, appName) {
  const body = openModal(`<h3>Logg – ${esc(title)}</h3><div class="term" id="lg">Laddar …</div>
    <div class="actions"><button class="btn" id="lgai">✨ Förklara loggen</button><button class="btn" id="rl"><svg><use href="#i-refresh"/></svg>Läs om</button><button class="btn primary" data-close>Stäng</button></div>`);
  let text = "";
  const load = async () => { text = await api(url); const el = $("#lg", body); if (!el) return; el.textContent = text || "(tom logg)"; el.scrollTop = el.scrollHeight; };
  $("#rl", body).onclick = load; load();
  $("#lgai", body).onclick = () => appName
    ? openAI(`Förklara vad loggen för ${title} säger. Är något fel, och vad ska jag i så fall göra?`, { fresh: true, app: appName })
    : openAI(`Förklara vad loggen för ${title} säger. Är något fel?`, { fresh: true, app: null, extra: text.slice(-6000) });
}

async function editCompose(a, reload) {
  const text = await api(`/api/apps/${a.name}/compose`);
  const body = openModal(`<h3>${esc(a.title)} – compose-fil</h3>
    <p class="hint">För den som vill ändra portar, mappar eller variabler för hand. Startar inte appen med ändringen återställs förra versionen.</p>
    <textarea id="cmp" spellcheck="false"></textarea>
    <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="sv">Spara och starta om</button></div>`);
  $("#cmp", body).value = text;
  $("#sv", body).onclick = async () => {
    try { const r = await api(`/api/apps/${a.name}/compose`, { method: "PUT", body: { compose: $("#cmp", body).value } }); watchJob(r.job, { title: `Sparar ${a.title}`, onDone: reload }); }
    catch (e) { toast(e.message, "bad"); }
  };
}

async function appDetails(key) {
  openModal(`<div class="empty"><span class="spin" style="display:inline-block"></span></div>`);
  let a;
  try { a = await api(`/api/store/app?key=${encodeURIComponent(key)}`); } catch (e) { closeModal(); toast(e.message, "bad"); return; }
  const f = a.form;
  const shots = a.screenshots.map(safeUrl).filter(Boolean);
  const body = openModal(`
    <div class="app-hero">${icon(a.icon, a.title)}<div><h3>${esc(a.title)}</h3><p>${esc(a.tagline)}</p>
      <p style="font-size:13px;margin-top:4px">${[a.developer, a.category, a.version && "v" + a.version].filter(Boolean).map(esc).join(" · ")}
      ${safeUrl(a.website) ? ` · <a href="${esc(a.website)}" target="_blank" rel="noopener noreferrer">Webbsida</a>` : ""}</p></div></div>
    ${shots.length ? `<div class="shots">${shots.map((s) => `<img src="${esc(s)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">`).join("")}</div>` : ""}
    <div class="desc">${esc(a.description || a.tagline)}</div>
    ${a.tips ? `<div class="tips"><b>Bra att veta:</b>\n${esc(a.tips)}</div>` : ""}
    ${!a.supported ? `<div class="warnbar">Den här appen finns inte för din processortyp.</div>` : ""}
    ${f.installed ? `<div class="tips">Det finns redan en app som heter <b>${esc(f.name)}</b>. Välj ett annat namn nedan för att installera en till.</div>` : ""}
    <div class="box"><label for="nm">Namn på servern</label><input id="nm" value="${esc(f.name)}"><p class="hint">Används för mappen /DATA/AppData/&lt;namn&gt;. Små bokstäver, siffror och bindestreck.</p>
      ${f.ports.length ? `<label>Portar</label><p class="hint" style="margin-top:-2px">Ändra bara om porten redan används (gul ram).</p>
        ${f.ports.map((p) => `<div class="kv"><div class="lab"><b>${esc(p.target)}/${esc(p.protocol)}</b>${esc(p.description || p.service)}</div>
          <input data-port="${esc(p.service)}:${esc(p.target)}/${esc(p.protocol)}" value="${esc(p.published)}" class="${p.busy ? "busy" : ""}" inputmode="numeric" placeholder="automatisk"></div>`).join("")}` : ""}
      ${f.envs.length ? `<details><summary>Avancerat: variabler (${f.envs.length})</summary>
        ${f.envs.map((e) => `<div class="kv"><div class="lab"><b>${esc(e.key)}</b>${esc(e.description)}</div><input data-env="${esc(e.service)}:${esc(e.key)}" value="${esc(e.value)}"></div>`).join("")}</details>` : ""}
    </div>
    <div class="trial-offer"><div class="trial-ico">🧪</div><div><b>Osäker? Prova först.</b>
      <span>Appen startar som vanligt, men tas bort spårlöst när tiden är slut – mappar, data och nedladdat program. Gillar du den trycker du på Behåll.</span></div>
      <div class="trial-pick"><select id="trm" aria-label="Provtid"><option value="30">30 min</option><option value="60" selected>1 timme</option><option value="180">3 timmar</option></select>
      <button class="btn" id="trial" ${a.supported ? "" : "disabled"}>Prova</button></div></div>
    <div class="actions"><button class="btn" data-close>Stäng</button><button class="btn primary" id="inst" ${a.supported ? "" : "disabled"}>Installera</button></div>`);
  const doInstall = async (trialMinutes) => {
    const ports = {}, envs = {};
    $$("[data-port]", body).forEach((i) => { ports[i.dataset.port] = i.value; });
    $$("[data-env]", body).forEach((i) => { envs[i.dataset.env] = i.value; });
    try {
      const r = await api("/api/install", { method: "POST", body: { key: a.key, name: $("#nm", body).value.trim().toLowerCase(), ports, envs, trial_minutes: trialMinutes } });
      storeState.data = null;
      watchJob(r.job, { title: trialMinutes ? `Provar ${a.title}` : `Installerar ${a.title}`, onDone: (j) => { if (j.state === "done") { const bar = $("#jc"); if (bar) { bar.insertAdjacentHTML("beforebegin", `<a class="btn primary" href="#/">Till mina appar</a>`); } } } });
    } catch (e) { toast(e.message, "bad"); }
  };
  $("#inst", body).onclick = () => doInstall(0);
  $("#trial", body).onclick = () => doInstall(parseInt($("#trm", body).value, 10));
}

async function safetyCard(el, reload) {
  if (!el) return;
  let r;
  try { r = await api("/api/safety"); } catch { return; }
  const dot = { ok: "🟢", warn: "🟡", bad: "🔴" };
  const dr = r.drill;
  const mins = dr ? Math.max(1, Math.round((dr.estimate_secs || 0) / 60)) : 0;
  el.innerHTML = `<h2>🛟 Hur säkra är mina filer?</h2>
    <div class="card list">
      ${r.rows.map((x) => `<div class="row"><div><div class="n">${dot[x.level]} ${esc(x.label)}${x.path ? ` <span class="hint">${esc(x.path)}</span>` : ""}</div>
        <div class="m">${x.copies} ${x.copies === 1 ? "kopia" : "kopior"}${x.outside ? " · en utanför huset" : ""}${x.verified ? " · provåterställd ✓" : ""}${x.size ? ` · ${fmtBytes(x.size)}` : ""} – ${esc(x.why)}</div></div>
        ${x.path ? `<div class="acts"><button class="icon-btn" data-sfrm="${esc(x.path)}" aria-label="Ta bort ur backupen" title="Ta bort ur backupen">✕</button></div>` : ""}</div>`).join("")}
      <div class="row"><div style="flex:1"><div class="n">➕ Lägg till en viktig mapp</div>
        <div class="m">T.ex. dina foton eller dokument. Den följer med i nattens backup.</div>
        <div style="display:flex;gap:8px;margin-top:6px;flex-wrap:wrap"><input id="sfpath" list="sfsug" placeholder="/DATA/Foton" style="max-width:280px">
        <datalist id="sfsug">${(r.suggest || []).map((p) => `<option value="${esc(p)}">`).join("")}</datalist>
        <button class="btn" id="sfadd">Lägg till</button></div></div></div>
    </div>
    <div class="card fade-in" style="margin-top:12px;padding:14px 16px"><div style="display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap">
      <div><b>🧯 Brandövning</b><div class="m">${dr ? (dr.ok ? `${esc(timeAgo(dr.time))}: allt kunde återställas. <b>Går servern sönder är du igång igen på ungefär ${mins} min.</b>`
        : `${esc(timeAgo(dr.time))}: <b style="color:var(--bad)">något gick inte att återställa</b> – ${esc([...dr.apps.filter((a) => !a.ok).map((a) => a.name), ...dr.folders.filter((f) => !f.ok).map((f) => f.path)].join(", "))}.`)
        : "En gång i månaden återställer WizzOS allt på prov – appar i en sandlåda och stickprov av dina filer – utan att röra originalen."}</div></div>
      <button class="btn" id="sfdrill">Kör brandövning nu</button></div></div>`;
  $("#sfadd", el).onclick = async () => {
    try { await api("/api/safety/folders", { method: "POST", body: { path: $("#sfpath", el).value } }); toast("Mappen följer med i nattens backup", "good"); reload(); } catch (e) { toast(e.message, "bad"); }
  };
  el.querySelectorAll("[data-sfrm]").forEach((b) => b.onclick = async () => {
    if (!confirm(`Sluta säkerhetskopiera ${b.dataset.sfrm}? Backuper som redan finns ligger kvar tills de gallras.`)) return;
    try { await api(`/api/safety/folders?path=${encodeURIComponent(b.dataset.sfrm)}`, { method: "DELETE" }); reload(); } catch (e) { toast(e.message, "bad"); }
  });
  $("#sfdrill", el).onclick = async () => {
    try { const j = await api("/api/safety/drill", { method: "POST" }); watchJob(j.job, { title: "Brandövning", onDone: reload }); } catch (e) { toast(e.message, "bad"); }
  };
}

function migrateUnraid() {
  const body = openModal(`<h3>📦 Flytta in från Unraid</h3>
    <p class="hint">WizzOS gör om dina Unraid-appar till WizzOS-appar – samma image, portar och inställningar. Unraids mappar byts mot WizzOS egna (<code>/mnt/user/appdata/…</code> → <code>/DATA/AppData/…</code>).</p>
    <ol class="hint" style="margin:.4rem 0 .8rem 1.1rem;line-height:1.6">
      <li>Öppna utdelningen <b>flash</b> på Unraid-servern (t.ex. <code>\\TOWER\flash</code>) och gå till <code>config/plugins/dockerMan/templates-user</code>.</li>
      <li>Markera filerna <b>my-….xml</b> för apparna du vill flytta (eller lägg alla i en zip-fil).</li>
      <li>Välj dem här. Inget installeras förrän du trycker på Installera vid en app.</li></ol>
    <input type="file" id="mf" accept=".xml,.zip" multiple>
    <div id="mres" style="margin-top:12px"></div>
    <div class="actions"><button class="btn" data-close>Stäng</button></div>`);
  $("#mf", body).onchange = async (ev) => {
    const res = $("#mres", body);
    res.innerHTML = `<p class="hint">Läser mallarna …</p>`;
    try {
      const files = await Promise.all([...ev.target.files].map((f) => new Promise((ok, bad) => {
        const r = new FileReader();
        r.onload = () => ok({ name: f.name, data: String(r.result).split(",")[1] || "" });
        r.onerror = bad; r.readAsDataURL(f);
      })));
      const r = await api("/api/migrate/unraid/scan", { method: "POST", body: { files } });
      res.innerHTML = r.items.map((it, i) => it.error ? `<div class="card" style="padding:10px;margin:8px 0">⚠️ <b>${esc(it.file)}</b>: ${esc(it.error)}</div>` : `
        <div class="card" style="padding:12px;margin:8px 0">
          <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
            📦
            <b>${esc(it.title)}</b><span class="hint">${esc(it.image)}${it.port ? ` · port ${esc(it.port)}` : ""}</span></div>
          ${it.exists ? `<p class="hint">Det finns redan en app med namnet <b>${esc(it.name)}</b> – välj ett annat namn.</p>` : ""}
          ${it.secrets.length ? `<p class="hint">🔒 ${it.secrets.length} lösenord/nycklar följer med men visas inte.</p>` : ""}
          ${it.warnings.map((w) => `<p class="hint">⚠️ ${esc(w)}</p>`).join("")}
          ${it.folders.length ? `<details><summary class="hint">📁 ${it.folders.length} mappar – så flyttar du innehållet</summary>
            <p class="hint">Stoppa appen på Unraid först. Kör sedan detta på den nya servern (byt <code>TOWER</code> mot Unraid-serverns adress):</p>
            <pre style="white-space:pre-wrap;font-size:.8rem">${it.folders.map((f) => esc(`rsync -a --info=progress2 root@TOWER:"${f.from}/" "${f.to}/"`)).join("\n")}</pre></details>` : ""}
          <details><summary class="hint">Visa inställningarna (docker-compose)</summary><pre style="white-space:pre-wrap;font-size:.8rem">${esc(it.compose)}</pre></details>
          <div style="display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap">
            <label class="hint" for="mn${i}">Namn</label><input id="mn${i}" value="${esc(it.name)}" style="max-width:220px">
            <button class="btn primary" data-mi="${i}">Installera</button></div>
        </div>`).join("");
      res.querySelectorAll("[data-mi]").forEach((btn) => {
        btn.onclick = async () => {
          const it = r.items[+btn.dataset.mi];
          try {
            const j = await api("/api/migrate/unraid/install", { method: "POST", body: { scan: r.scan, file: it.file, name: $(`#mn${btn.dataset.mi}`, body).value } });
            btn.disabled = true; btn.textContent = "Installeras …";
            watchJob(j.job, { title: `Flyttar in ${it.title}` });
          } catch (e) { toast(e.message, "bad"); }
        };
      });
    } catch (e) { res.innerHTML = ""; toast(e.message, "bad"); }
  };
}

function customApp() {
  const body = openModal(`<h3>Egen app</h3><p class="hint">Klistra in en docker-compose-fil, t.ex. från en apps GitHub-sida.</p>
    <div class="grid2"><div><label for="cn">Namn (litet, utan mellanslag)</label><input id="cn" placeholder="minapp"></div>
    <div><label for="ct">Visningsnamn</label><input id="ct" placeholder="Min app"></div>
    <div><label for="cp">Webbport (valfritt)</label><input id="cp" placeholder="8080" inputmode="numeric"></div>
    <div><label for="ci">Ikon-adress (valfritt)</label><input id="ci" placeholder="https://…/icon.png"></div></div>
    <label for="cc">docker-compose.yml</label><textarea id="cc" spellcheck="false" placeholder="services:\n  app:\n    image: …"></textarea>
    <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="ci-go">Installera</button></div>`);
  $("#ci-go", body).onclick = async () => {
    try {
      const r = await api("/api/custom", { method: "POST", body: { name: $("#cn", body).value, title: $("#ct", body).value, port: $("#cp", body).value, icon: $("#ci", body).value, compose: $("#cc", body).value } });
      watchJob(r.job, { title: "Installerar egen app" });
    } catch (e) { toast(e.message, "bad"); }
  };
}

/* ---------------- AI-hjälp ---------------- */

const ai = { messages: [], app: null, extra: null, busy: false, cfg: null };
try { ai.messages = JSON.parse(sessionStorage.getItem("wz-ai") || "[]"); } catch { ai.messages = []; }
const aiSave = () => { try { sessionStorage.setItem("wz-ai", JSON.stringify(ai.messages.slice(-20))); } catch { /* privat läge */ } };

function md(text) {
  // Enkel och säker markdown: allt escapas först.
  let h = esc(text);
  h = h.replace(/```[a-z]*\n?([\s\S]*?)```/g, (_, c) => `<pre>${c}</pre>`);
  h = h.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  h = h.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  h = h.replace(/^#{1,4} (.+)$/gm, "<b>$1</b>");
  h = h.replace(/^(?:[-*] .+\n?)+/gm, (blk) => `<ul>${blk.trim().split("\n").map((l) => `<li>${l.replace(/^[-*] /, "")}</li>`).join("")}</ul>`);
  h = h.replace(/^(?:\d+\. .+\n?)+/gm, (blk) => `<ol>${blk.trim().split("\n").map((l) => `<li>${l.replace(/^\d+\. /, "")}</li>`).join("")}</ol>`);
  return h.split(/\n{2,}/).map((p) => (/^<(ul|ol|pre)/.test(p) ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`)).join("");
}

const AI_ACTIONS = {
  "installera": (v) => t("Installera {a}", { a: v.split("/").pop() }), "öppna": (v) => t("Öppna {a}", { a: v }), "starta": (v) => t("Starta {a}", { a: v }),
  "stoppa": (v) => t("Stoppa {a}", { a: v }), "starta-om": (v) => t("Starta om {a}", { a: v }), "uppdatera": (v) => t("Uppdatera {a}", { a: v }),
  "logg": (v) => t("Visa loggen för {a}", { a: v }), "ångra": (v) => t("Ångra – {a}", { a: v }), "backup": (v) => t("Backup av {a}", { a: v }),
};

function aiRenderReply(text) {
  const actions = [];
  const clean = text.replace(/\[\[([a-zåäö-]+):([^\]\s]+)\]\]/gi, (m, a, v) => {
    a = a.toLowerCase();
    if (AI_ACTIONS[a] && !actions.some((x) => x.a === a && x.v === v)) actions.push({ a, v });
    return "";
  }).replace(/\n{3,}/g, "\n\n").trim();
  return md(clean) + (actions.length ? `<div class="ai-acts">${actions.map((x) =>
    `<button class="btn" data-ai-a="${esc(x.a)}" data-ai-v="${esc(x.v)}">${esc(AI_ACTIONS[x.a](x.v))}</button>`).join("")}</div>` : "");
}

async function aiDoAction(a, v) {
  try {
    if (a === "installera") return appDetails(v);
    const d = await api("/api/apps");
    const app = d.apps.find((x) => x.name === v);
    if (!app) return toast(`Hittar ingen installerad app som heter ${v}.`, "bad");
    const reload = () => { if (location.hash === "" || location.hash === "#/") route(); };
    if (a === "öppna") return openApp(app);
    if (a === "logg") return showLogs(`/api/apps/${app.name}/logs`, app.title, app.name);
    if (a === "ångra") return showHistory(app, reload);
    if (a === "backup") {
      const r = await api(`/api/backup/apps/${app.name}/backup`, { method: "POST" });
      return watchJob(r.job, { title: `Backup av ${app.title}` });
    }
    const what = { "starta": "start", "stoppa": "stop", "starta-om": "restart", "uppdatera": "update" }[a];
    if (!await confirmBox(`${AI_ACTIONS[a](app.title)}?`, "Assistenten föreslog det här. Vill du göra det?", "Ja, gör det")) return;
    const r = await api(`/api/apps/${app.name}/${what}`, { method: "POST" });
    if (r.job) watchJob(r.job, { title: `Uppdaterar ${app.title}`, onDone: reload });
    else { toast(`${app.title}: klart`, "good"); reload(); }
  } catch (e) { toast(e.message, "bad"); }
}

function aiDraw() {
  const box = $("#ai-msgs");
  if (!ai.cfg || !ai.cfg.configured) {
    box.innerHTML = `<div class="ai-empty"><div class="ai-big">✨</div><p>AI-hjälpen är inte inställd än.</p>
      <a class="btn primary" href="#/settings" onclick="document.getElementById('ai-panel').classList.add('hidden')">Ställ in den</a></div>`;
    return;
  }
  const chips = ["Hur mår servern?", "Är backupen i ordning?", "Vilken app passar för mina filmer?", "Vad kan jag göra med WizzOS?"];
  box.innerHTML = ai.messages.length ? ai.messages.map((m) => m.role === "user"
    ? `<div class="ai-msg me" translate="no">${esc(m.content)}</div>`
    : `<div class="ai-msg bot" translate="no">${aiRenderReply(m.content)}</div>`).join("") + (ai.busy ? `<div class="ai-msg bot"><span class="dots"><i></i><i></i><i></i></span></div>` : "")
    : `<div class="ai-empty"><div class="ai-big">✨</div><p>Hej! Jag kan förklara vad som händer på servern, tolka loggar och tipsa om appar. Jag gör inget utan att du trycker.</p>
      <div class="ai-chips">${chips.map((c) => `<button class="chip" data-q="${esc(c)}">${esc(c)}</button>`).join("")}</div></div>`;
  $$("[data-q]", box).forEach((b) => b.onclick = () => aiAsk(t(b.dataset.q)));
  $$("[data-ai-a]", box).forEach((b) => b.onclick = () => aiDoAction(b.dataset.aiA, b.dataset.aiV));
  box.scrollTop = box.scrollHeight;
}

async function aiAsk(q, opts = {}) {
  q = (q || "").trim();
  if (!q || ai.busy) return;
  if (opts.app !== undefined) ai.app = opts.app;
  if (opts.extra !== undefined) ai.extra = opts.extra;
  ai.messages.push({ role: "user", content: q });
  ai.busy = true; aiDraw(); aiSave();
  try {
    const r = await api("/api/ai/chat", { method: "POST", body: { messages: ai.messages, app: ai.app, extra: ai.extra } });
    ai.messages.push({ role: "assistant", content: r.reply });
  } catch (e) {
    ai.messages.push({ role: "assistant", content: `⚠️ ${e.message}` });
  }
  ai.busy = false; ai.extra = null; aiDraw(); aiSave();
}

async function openAI(q, opts = {}) {
  closeModal();
  $("#ai-panel").classList.remove("hidden");
  try { ai.cfg = await api("/api/ai"); } catch { /* visas i panelen */ }
  $("#ai-model").textContent = ai.cfg && ai.cfg.configured ? ai.cfg.model : "";
  if (opts.fresh) { ai.messages = []; ai.app = null; }
  aiDraw();
  if (q) aiAsk(q, opts); else $("#ai-input").focus();
}

$("#ai-fab").onclick = () => ($("#ai-panel").classList.contains("hidden") ? openAI() : $("#ai-panel").classList.add("hidden"));
$("#ai-close").onclick = () => $("#ai-panel").classList.add("hidden");
$("#ai-new").onclick = () => { ai.messages = []; ai.app = null; ai.extra = null; aiSave(); aiDraw(); $("#ai-input").focus(); };
$("#ai-form").onsubmit = (e) => { e.preventDefault(); const t = $("#ai-input"); const q = t.value; t.value = ""; t.style.height = ""; aiAsk(q); };
$("#ai-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ai-form").requestSubmit(); } });
$("#ai-input").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(140, e.target.scrollHeight) + "px"; });

/* ---------------- Start ---------------- */

async function route() {
  clearTimers(); closeMenu(); $("#view").classList.remove("settled");
  const page = (location.hash.replace(/^#\/?/, "").split("/")[0]) || "home";
  const P = window.ME && window.ME.role !== "admin" ? limitedPages : pages;
  const fn = P[page] || P.home;
  $$(".side a[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === (P[page] ? page : "home")));
  const view = $("#view");
  // Ny sida = börja högst upp (annars öppnas t.ex. Inställningar mitt i).
  if (page !== route.last) window.scrollTo(0, 0);
  route.last = page;
  try { await fn(view); } catch (e) { if (e.message !== "Inte inloggad") view.innerHTML = `<div class="warnbar">${esc(e.message)}</div>`; }
  if (page === "settings") settingsToc();
}

/* Genvägar högst upp på den långa inställningssidan */
function settingsToc() {
  const wrap = $("#view .settings"); if (!wrap || $("#stoc")) return;
  const cards = $$(":scope > .card", wrap).filter((c) => $("h2", c));
  const name = (c) => $("h2", c).childNodes[0].textContent.trim().replace(/\s+/g, " ");
  cards.forEach((c, i) => { if (!c.id) c.id = `s-${i}`; c.style.scrollMarginTop = "72px"; });
  wrap.insertAdjacentHTML("beforebegin", `<nav class="stoc" id="stoc" aria-label="Hoppa till">${cards.map((c) => `<button type="button" data-to="${esc(c.id)}">${esc(name(c))}</button>`).join("")}</nav>`);
  $$("#stoc [data-to]").forEach((b) => b.onclick = () => document.getElementById(b.dataset.to)?.scrollIntoView({ behavior: "smooth", block: "start" }));
}

let sideClock = null;
async function startApp() {
  try { window.ME = await api("/api/me"); } catch (e) { return; }
  applyRole();
  const ss = $("#side-search"); if (ss) ss.onclick = () => openFinder();
  if (!sideClock) { tickClock(); sideClock = setInterval(tickClock, 1000); }
  $("#auth").classList.add("hidden");
  $("#shell").classList.remove("hidden");
  $("#ai-fab").classList.remove("hidden");
  api("/api/proxy").then((px) => {
    if (px.enabled && /^(100\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.)/.test(px.ip || "")) window.WIZZ_PRIVATE_DOMAIN = px.domain;
  }).catch(() => {});
  route();
}

window.addEventListener("hashchange", () => { closeModal(); route(); });

(async () => {
  await window.I18N_READY;
  try {
    const s = await api("/api/state");
    window.WIZZ_VERSION = s.version; $("#ver").textContent = `v${s.version}`; window.WIZZ_PEOPLE = s.people || [];
    if (s.setup) showAuth(true); else if (!s.logged_in) showAuth(false); else startApp();
  } catch (e) { document.body.insertAdjacentHTML("beforeend", `<p class="empty">Kan inte nå servern.</p>`); }
})();
