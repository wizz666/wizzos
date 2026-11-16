/* WizzOS – egen sökmotor (SearXNG-paketet): gör den till standardsökmotor. */
"use strict";

function searchEngineModal(a) {
  const base = (a.url || appUrl(a)).replace(/\/$/, "");
  const q = `${base}/search?q=%s`;
  const priv = isPrivUrl(base);
  openModal(`<h3>🔍 Gör ${esc(a.title.split(" –")[0])} till standardsökmotor</h3>
    <p>Då hamnar allt du skriver i adressfältet i din egen sökmotor i stället för hos Google – utan spårning och reklam.</p>
    <label>Sökadress</label><div class="copyline"><code>${esc(q)}</code><button class="btn" data-cp="${esc(q)}">Kopiera</button></div>
    <div class="howto" style="margin-top:14px">
      <div><b>Chrome / Edge / Brave (dator)</b><span>Öppna <b>Inställningar → Sökmotor → Hantera sökmotorer</b> → <b>Lägg till</b>. Namn: <i>WizzOS Sök</i>, kortkommando: <i>s</i>, adress: sökadressen ovan. Tryck sedan <b>⋮ → Ange som standard</b>.</span></div>
      <div><b>Firefox</b><span>Öppna sökmotorn, högerklicka i adressfältet och välj <b>Lägg till "WizzOS Sök"</b>. Välj den sedan under <b>Inställningar → Sök</b>.</span></div>
      <div><b>🤖 Android (Chrome)</b><span>Skanna koden, gör en sökning, och välj den sedan under <b>Inställningar → Sökmotor</b> (under "Senast besökta").</span></div>
      <div><b> iPhone</b><span>Safari tillåter bara sina egna sökmotorer. Lägg i stället sökmotorn på hemskärmen (skanna koden → dela ⬆︎ → <b>Lägg till på hemskärmen</b>), eller använd Firefox för iOS.</span></div>
    </div>
    <div class="phone" style="margin-top:14px"><div class="phone-qr"><img src="/api/qr?text=${encodeURIComponent(base)}" alt="QR-kod till sökmotorn" width="180" height="180"></div>
      <div><b>📱 Till mobilen</b><p class="hint">Rikta kameran mot koden.</p>
      ${priv ? `<p class="hint">🔒 Adressen är privat – mobilen och datorn behöver Tailscale eller vara hemma för att sökmotorn ska fungera.</p>` : ""}</div></div>
    <div class="actions"><a class="btn" href="${esc(base)}" target="_blank" rel="noopener">Öppna sökmotorn</a><button class="btn primary" data-close>Klar</button></div>`);
  $$("#modal [data-cp]").forEach((b) => b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.cp); toast("Kopierat", "good"); } catch { toast(b.dataset.cp); } });
}

/* ---------------- Dokument (inställningar) ---------------- */

async function docCard() {
  const box = $("#doccard"); if (!box) return;
  const d = await api("/api/documents");
  const ix = d.index, p = d.paperless;
  box.innerHTML = `<h2>📄 Dokument</h2>
    <p class="hint" style="margin-top:-6px">WizzOS läser texten i dina dokument så att du kan söka i innehållet med <b>Ctrl K</b> – inte bara i filnamnen.
      Typer: ${d.types.map((t) => t.slice(1).toUpperCase()).join(", ")}. Inskannade papper utan text söks via Paperless.</p>
    <div class="doc-stats"><div><b>${(ix.indexed || 0).toLocaleString("sv-SE")}</b><span>dokument sökbara</span></div>
      <div><b>${(ix.no_text || 0).toLocaleString("sv-SE")}</b><span>utan text (inskannade?)</span></div>
      <div><b>${ix.db_mb || 0} MB</b><span>index</span></div>
      <div><b>${ix.running ? "Pågår …" : ix.last ? esc(timeAgo(ix.last)) : "Inte än"}</b><span>${ix.running ? esc((ix.current || "").split("/").pop()) : "senast uppdaterat"}</span></div></div>
    <div class="actions" style="justify-content:flex-start"><button class="btn" id="docre">Uppdatera nu</button></div>
    <label class="radio"><input type="checkbox" id="docai" ${d.ai_read_docs ? "checked" : ""}><div><b>Låt AI-kopplingen söka i och läsa dina dokument</b>
      <span>Då kan Claude svara på t.ex. "vad är uppsägningstiden i hyresavtalet?". Av som standard – dokument kan vara privata. Varje läsning syns i AI-dagboken.</span></div></label>
    <h2 style="margin-top:22px">🗂️ Paperless ${p.configured ? (p.ok === false ? '<span class="badge no">Fel</span>' : '<span class="badge ok">Kopplad</span>') : ""}</h2>
    <p class="hint" style="margin-top:-6px">Har du Paperless-ngx (inskannade papper med OCR)? Koppla den så söker Ctrl K även där.</p>
    <div class="grid2"><div><label for="plu">Adress</label><input id="plu" value="${esc(p.url)}" placeholder="https://paperless.exempel.se"></div>
      <div><label for="plt">API-token</label><input id="plt" type="password" autocomplete="off" placeholder="${p.has_token ? "Sparad – lämna tomt för att behålla" : "Paperless → din profil → API-token"}"></div></div>
    <div class="actions">${p.url ? '<button class="btn danger" id="ploff">Koppla bort</button>' : ""}<button class="btn primary" id="plsave">Spara och testa</button></div>`;
  $("#docre").onclick = async () => { await api("/api/documents/reindex", { method: "POST" }); toast("Uppdaterar indexet i bakgrunden", "good"); setTimeout(docCard, 3000); };
  $("#docai").onchange = async (e) => { try { await api("/api/documents", { method: "PUT", body: { ai_read_docs: e.target.checked } }); toast(e.target.checked ? "AI får läsa dina dokument" : "AI får inte läsa dina dokument", "good"); } catch (er) { toast(er.message, "bad"); } };
  $("#plsave").onclick = async () => {
    try { await api("/api/paperless", { method: "PUT", body: { url: $("#plu").value, token: $("#plt").value } }); $("#plt").value = "";
      const r = await api("/api/paperless/test", { method: "POST" }); toast(`Kopplad! ${r.documents} dokument i Paperless`, "good"); docCard(); } catch (e) { toast(e.message, "bad"); docCard(); }
  };
  $("#ploff")?.addEventListener("click", async () => { await api("/api/paperless", { method: "PUT", body: { url: "" } }); toast("Bortkopplad", "good"); docCard(); });
}

/* ---------------- Sök i hela servern (Ctrl K) ---------------- */

const finder = { q: "", seq: 0, sel: 0, web: null };
const fmtSize = (b) => (b ? fmtBytes(b) : "");

function openFinder(initial = "") {
  closeFinder();
  document.body.insertAdjacentHTML("beforeend", `<div id="finder" class="finder" role="dialog" aria-modal="true" aria-label="Sök">
    <div class="fd-card">
      <div class="fd-top"><svg><use href="#i-search"/></svg><input id="fd-q" placeholder="Sök i appar, filer, inställningar och på webben …" autocomplete="off" spellcheck="false" value="${esc(initial)}"><kbd>Esc</kbd></div>
      <div class="fd-res" id="fd-res"><div class="fd-hint">Skriv minst två tecken. Tips: <b>Ctrl K</b> öppnar sökningen var du än är.</div></div>
    </div></div>`);
  const inp = $("#fd-q");
  inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
  let t;
  inp.oninput = () => { clearTimeout(t); t = setTimeout(() => finderRun(inp.value), 220); };
  inp.onkeydown = (e) => {
    const items = $$("#fd-res .fd-item");
    if (e.key === "ArrowDown") { e.preventDefault(); finder.sel = Math.min(items.length - 1, finder.sel + 1); finderMark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); finder.sel = Math.max(0, finder.sel - 1); finderMark(); }
    else if (e.key === "Enter") { e.preventDefault(); const it = items[finder.sel]; if (it) it.click(); }
    else if (e.key === "Escape") closeFinder();
  };
  $("#finder").onclick = (e) => { if (e.target.id === "finder") closeFinder(); };
  if (initial) finderRun(initial);
}
function closeFinder() { $("#finder")?.remove(); }
function finderMark() { $$("#fd-res .fd-item").forEach((el, i) => { el.classList.toggle("sel", i === finder.sel); if (i === finder.sel) el.scrollIntoView({ block: "nearest" }); }); }

async function finderRun(q) {
  q = q.trim(); finder.q = q; const my = ++finder.seq; finder.sel = 0;
  const box = $("#fd-res"); if (!box) return;
  if (q.length < 2) { box.innerHTML = `<div class="fd-hint">Skriv minst två tecken.</div>`; return; }
  let local;
  try { local = await api(`/api/find?q=${encodeURIComponent(q)}&web=0`); } catch (e) { box.innerHTML = `<div class="fd-hint">${esc(e.message)}</div>`; return; }
  if (my !== finder.seq) return;
  const needNet = local.web.available || local.paperless_on;
  finderDraw(local, local.web.available ? null : local.web, local.paperless_on ? null : []);
  if (!needNet) return;
  try { const w = await api(`/api/find?q=${encodeURIComponent(q)}&web=1`); if (my === finder.seq) finderDraw(local, w.web, w.paperless); } catch { /* */ }
}

const markSnip = (s) => esc(s || "").replace(/\x02/g, "<mark>").replace(/\x03/g, "</mark>");

function finderDraw(r, web, pl) {
  const box = $("#fd-res"); if (!box) return;
  finder.web = web;
  const sec = (title, rows) => rows.length ? `<div class="fd-sec">${title}</div>${rows.join("")}` : "";
  const parent = (p) => p.replace(/\/[^/]*$/, "") || "/";
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
  const apps = r.apps.map((a) => `<button class="fd-item" data-kind="app" data-name="${esc(a.name)}">${icon(a.icon, a.title, "fd-ico")}<span><b>${esc(a.title)}</b><small>${a.trial ? "🧪 på prov · " : ""}${esc(STATUS[a.status] || a.status || "")}</small></span><i>Öppna ↗</i></button>`);
  const places = r.places.map((p) => `<button class="fd-item" data-kind="place" data-link="${esc(p.link)}"><span class="fd-emo">⚙️</span><span><b>${esc(p.title)}</b><small>I WizzOS</small></span><i>Gå dit</i></button>`);
  const files = r.files.map((f) => `<button class="fd-item" data-kind="file" data-path="${esc(f.path)}" data-dir="${f.dir ? 1 : ""}"><span class="fd-emo">${fileIcon({ name: f.name, dir: f.dir })}</span><span><b>${esc(f.name)}</b><small>${esc(parent(f.path))}${f.dir ? "" : " · " + esc(fmtSize(f.size))}</small></span><i>${f.dir ? "Öppna mapp" : "Visa i mapp"}</i></button>`);
  const docs = (r.docs || []).map((d) => `<button class="fd-item fd-doc" data-kind="file" data-path="${esc(d.path)}"><span class="fd-emo">${fileIcon({ name: d.name, dir: false })}</span><span><b>${esc(d.name)}</b><small class="fd-snip">${markSnip(d.snippet)}</small><small>${esc(parent(d.path))}</small></span><i>Visa i mapp</i></button>`);
  let plRows = [];
  if (pl === null) plRows = [`<div class="fd-hint"><span class="spin"></span> Söker i Paperless …</div>`];
  else plRows = (pl || []).map((d) => `<a class="fd-item fd-doc" href="${esc(safeUrl(d.url))}" target="_blank" rel="noopener"><span class="fd-emo">🗂️</span><span><b>${esc(d.title)}</b><small class="fd-snip">${markSnip(d.snippet) || "&nbsp;"}</small><small>Paperless${d.created ? " · " + esc(d.created) : ""}</small></span><i>Öppna ↗</i></a>`);
  const store = r.store.map((s) => `<button class="fd-item" data-kind="store" data-key="${esc(s.key)}">${icon(s.icon, s.title, "fd-ico")}<span><b>${esc(s.title)}</b><small>${esc(s.tagline)}</small></span><i>Installera …</i></button>`);
  let webRows = [];
  if (web === null) webRows = [`<div class="fd-hint"><span class="spin"></span> Söker på webben …</div>`];
  else if (web.available) {
    if (web.answer) webRows.push(`<div class="fd-answer">💡 ${esc(web.answer)}</div>`);
    webRows = webRows.concat((web.results || []).map((w) => `<a class="fd-item" href="${esc(safeUrl(w.url))}" target="_blank" rel="noopener noreferrer"><span class="fd-emo">🌐</span><span><b>${esc(w.title)}</b><small>${esc(w.content || w.url)}</small></span><i>${esc(host(w.url))}</i></a>`));
    if (web.error) webRows.push(`<div class="fd-hint">${esc(web.error)}</div>`);
    if (web.more) webRows.push(`<a class="fd-item fd-more" href="${esc(web.more)}" target="_blank" rel="noopener"><span class="fd-emo">🔍</span><span><b>Fler resultat i din sökmotor</b><small>Privat sökning via SearXNG – utan spårning</small></span><i>↗</i></a>`);
  } else webRows = [`<button class="fd-item" data-kind="store" data-key="wizzos/searxng"><span class="fd-emo">🔍</span><span><b>Sök även på webben – privat</b><small>Installera din egen sökmotor (SearXNG) från WizzOS-paketen</small></span><i>Installera …</i></button>`];
  const empty = !apps.length && !places.length && !files.length && !docs.length && !store.length && web && web.available && !(web.results || []).length && !(pl || []).length;
  box.innerHTML = sec("Mina appar", apps) + sec("WizzOS", places) + sec("I dokument", docs) + sec("I Paperless", plRows)
    + sec(`Filnamn${r.index?.count ? ` <small>${r.index.count.toLocaleString("sv-SE")} filer i indexet</small>` : ""}`, files)
    + sec("I appbutiken", store) + sec("Webben", webRows) + (empty ? `<div class="fd-hint">Inget hittades för "${esc(r.q)}".</div>` : "");
  $$("#fd-res .fd-item").forEach((el, i) => {
    el.onmouseenter = () => { finder.sel = i; finderMark(); };
    if (el.tagName === "A") { el.addEventListener("click", () => setTimeout(closeFinder, 50)); return; }
    el.onclick = async () => {
      const k = el.dataset.kind;
      closeFinder();
      if (k === "app") { const d = await api("/api/apps"); const a = d.apps.find((x) => x.name === el.dataset.name); if (a) openApp(a); }
      else if (k === "place") location.hash = el.dataset.link;
      else if (k === "file") location.hash = "#/files" + encP(el.dataset.dir ? el.dataset.path : parent(el.dataset.path));
      else if (k === "store") appDetails(el.dataset.key);
    };
  });
  finderMark();
}

document.addEventListener("keydown", (e) => {
  if (document.body.classList.contains("limited")) return;
  if ($("#shell")?.classList.contains("hidden")) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "") || document.activeElement?.isContentEditable;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if ($("#finder")) closeFinder(); else openFinder(); }
  else if (e.key === "/" && !typing && !$("#finder") && $("#modal")?.classList.contains("hidden")) { e.preventDefault(); openFinder(); }
});
