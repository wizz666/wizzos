/* WizzOS – Trygghet: säkerhetskoll, delningar med utgångsdatum, "Om jag försvinner"
   och "Vad kostar min server?". Laddas efter app.js och använder dess hjälpfunktioner. */
"use strict";

const kr = (n, d = 0) => Number(n || 0).toLocaleString(loc(), { maximumFractionDigits: d, minimumFractionDigits: d });
const LEVEL = { bad: "🔴", warn: "🟡", ok: "🟢" };
const isPrivUrl = (u) => { try { const h = new URL(u).hostname; return /^(100\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.|localhost)/.test(h) || /ts\.net$/.test(h) || (window.WIZZ_PRIVATE_DOMAIN && h.endsWith(window.WIZZ_PRIVATE_DOMAIN)); } catch { return true; } };
const fmtDay = (t) => new Date(t * 1000).toLocaleDateString(loc(), { day: "numeric", month: "long", year: "numeric" });
const leftTxt = (t) => { const s = t - Date.now() / 1000; if (s <= 0) return "har gått ut"; const d = Math.floor(s / 86400); return d >= 2 ? `${d} dagar kvar` : s >= 3600 ? `${Math.floor(s / 3600)} timmar kvar` : `${Math.max(1, Math.floor(s / 60))} min kvar`; };

async function shareBase() {
  try { const px = await api("/api/proxy"); if (px.enabled && px.wizz_url) return px.wizz_url.replace(/\/$/, ""); } catch { /* */ }
  return location.origin;
}

/* ---------------- Dela med utgångsdatum ---------------- */

function shareModal(path, name, isDir) {
  openModal(`<h3>🔗 Dela ${isDir ? "mappen" : ""} ${esc(name)}</h3>
    <p class="hint">Den du skickar länken till kan öppna och ladda ner ${isDir ? "allt i mappen" : "filen"} – utan konto. Inget annat på servern syns.</p>
    <label>Länken fungerar i</label>
    <div class="seg" id="shd">${[[1, "1 dag"], [7, "1 vecka"], [30, "30 dagar"], [90, "90 dagar"]].map(([d, t]) => `<button type="button" data-d="${d}" class="${d === 7 ? "on" : ""}">${t}</button>`).join("")}</div>
    <label for="shp">Kod (valfritt, 4–8 siffror)</label><input id="shp" inputmode="numeric" maxlength="8" autocomplete="off" placeholder="Ingen kod">
    <p class="hint">Med en kod räcker det inte att få tag på länken. Skicka koden på ett annat sätt, t.ex. sms.</p>
    <label for="shn">Hälsning (valfritt)</label><textarea id="shn" rows="2" maxlength="300" placeholder="Här är bilderna från midsommar!"></textarea>
    <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="shgo">Skapa länk</button></div>`);
  let days = 7;
  $$("#shd button").forEach((b) => b.onclick = () => { days = +b.dataset.d; $$("#shd button").forEach((x) => x.classList.toggle("on", x === b)); });
  $("#shgo").onclick = async () => {
    try {
      const r = await api("/api/shares", { method: "POST", body: { path, days, pin: $("#shp").value.trim(), note: $("#shn").value } });
      showShareLink(r, await shareBase(), $("#shp").value.trim());
    } catch (e) { toast(e.message, "bad"); }
  };
}

function showShareLink(s, base, pin = "") {
  const url = base + s.url_path;
  openModal(`<div class="phone">
    <div class="phone-qr"><img src="/api/qr?text=${encodeURIComponent(url)}" alt="QR-kod till delningen" width="220" height="220"></div>
    <div><h3>✅ Länken är klar</h3>
      <p>${esc(s.name)} kan öppnas fram till <b>${esc(fmtDay(s.expires))}</b>. Sedan slutar länken fungera av sig själv.</p>
      <p class="mono-url">${esc(url)}</p>
      ${pin ? `<p class="hint">🔒 Koden är <b>${esc(pin)}</b> – skicka den separat.</p>` : ""}
      ${isPrivUrl(url) ? `<p class="hint">⚠️ Adressen är privat (Tailscale/hemnätet). Mottagaren når den bara om hen är på samma nätverk. Ge WizzOS en egen adress med https för att dela med vem som helst.</p>` : ""}
      <div class="actions" style="justify-content:flex-start"><button class="btn primary" id="shcp">Kopiera länken</button>
        ${navigator.share ? '<button class="btn" id="shsh">Dela …</button>' : ""}<a class="btn" href="#/trygghet">Alla delningar</a></div>
    </div></div>`);
  $("#shcp").onclick = async () => { try { await navigator.clipboard.writeText(url); toast("Länken är kopierad", "good"); } catch { toast(url); } };
  $("#shsh")?.addEventListener("click", () => navigator.share({ title: s.name, url }).catch(() => {}));
}

/* ---------------- Vad kostar min server? ---------------- */

async function costModal() {
  let c;
  try { c = await api("/api/cost"); } catch (e) { toast(e.message, "bad"); return; }
  const vps = c.kind === "vps";
  const max = Math.max(...c.rows.map((r) => r.month_kr), 0.01);
  openModal(`<h3>⚡ Vad kostar min server?</h3>
    <div class="cost-big"><b>≈ ${kr(c.month_kr)} kr</b><span>i månaden · ≈ ${kr(c.year_kr)} kr om året</span></div>
    <p class="hint">${vps ? `Hyrd server: ${kr(c.cfg.vps_month)} kr/mån fördelat efter hur mycket varje app använder.`
      : `Just nu drar servern ungefär <b>${kr(c.watts_now, 1)} W</b>. Elpris ${kr(c.price, 2)} kr/kWh ${c.price_source === "live" ? "(live från Home Assistant)" : "(fast pris)"}.`}
      ${c.measured_hours < 24 ? ` Mätt i ${kr(c.measured_hours, 1)} timmar – siffrorna blir säkrare efter ett dygn.` : ""}</p>
    ${c.price_error ? `<div class="warnbar">${esc(c.price_error)}</div>` : ""}
    <div class="cost-rows">${c.rows.map((r) => `<div class="cost-row ${r.key.startsWith("_") ? "sys" : ""}">
      <div class="cn">${esc(r.title)}${r.watts != null ? `<small>${kr(r.watts, 1)} W</small>` : ""}</div>
      <div class="cb"><i style="width:${Math.max(2, r.month_kr / max * 100)}%"></i></div>
      <div class="ck">${r.month_kr < 1 ? "< 1" : kr(r.month_kr)} kr</div></div>`).join("")}</div>
    ${c.tip ? `<p class="cost-tip">💡 ${esc(c.tip)}</p>` : ""}
    <details ${c.measured_hours < 1 ? "open" : ""}><summary>Ställ in din dator och ditt elpris</summary>
      <label for="ck">Vilken sorts dator är det?</label>
      <select id="ck">${Object.entries(c.presets).map(([k, p]) => `<option value="${k}" ${k === c.kind ? "selected" : ""}>${esc(p.title)}${p.idle_w != null ? ` (${p.idle_w}–${p.max_w} W)` : ""}</option>`).join("")}</select>
      <div class="grid2" id="cw"><div><label for="ci">Tomgång (W)</label><input id="ci" type="number" min="0" value="${c.cfg.idle_w}"></div>
        <div><label for="cm">Full fart (W)</label><input id="cm" type="number" min="0" value="${c.cfg.max_w}"></div></div>
      <div id="cv"><label for="cvm">Vad kostar servern i månaden (kr)?</label><input id="cvm" type="number" min="0" value="${c.cfg.vps_month}"></div>
      <div id="cp"><div class="grid2"><div><label for="cpr">Fast elpris (kr/kWh, allt inräknat)</label><input id="cpr" type="number" step="0.01" min="0" value="${c.cfg.price}"></div>
        <div><label for="che">…eller live från Home Assistant</label><input id="che" value="${esc(c.cfg.ha_entity)}" placeholder="sensor.electricity_price"></div></div>
        <p class="hint">Med en elprissensor (t.ex. Tibber eller Nord Pool) följer kostnaden timpriset. Kräver att Home Assistant är kopplad under Inställningar. Ta med nätavgift och skatt i det fasta priset – runt 2 kr är vanligt.</p></div>
      <div class="actions"><button class="btn primary" id="csave">Spara</button></div></details>`);
  const sync = () => { const k = $("#ck").value; $("#cw").classList.toggle("hidden", k !== "custom"); $("#cv").classList.toggle("hidden", k !== "vps"); $("#cp").classList.toggle("hidden", k === "vps"); };
  $("#ck").onchange = sync; sync();
  $("#csave").onclick = async () => {
    try {
      await api("/api/cost", { method: "PUT", body: { kind: $("#ck").value, idle_w: $("#ci").value, max_w: $("#cm").value, price: $("#cpr").value, ha_entity: $("#che").value, vps_month: $("#cvm").value } });
      toast("Sparat – kostnaden räknas med de nya värdena från och med nu", "good"); costModal();
    } catch (e) { toast(e.message, "bad"); }
  };
}

async function drawCostCard() {
  const el = $("#costcard"); if (!el) return;
  try {
    const c = await api("/api/cost");
    $("#v-cost").textContent = `≈ ${kr(c.month_kr)} kr/mån`;
    $("#s-cost").textContent = c.kind === "vps" ? "hyrd server" : `${kr(c.watts_now, 0)} W just nu`;
  } catch { /* */ }
}

/* ---------------- Sidan Trygghet ---------------- */

pages.trygghet = async function (view) {
  const [sec, sh, lg, base] = await Promise.all([api("/api/security"), api("/api/shares"), api("/api/legacy"), shareBase()]);
  const col = sec.score >= 90 ? "var(--good)" : sec.score >= 60 ? "var(--warn)" : "var(--bad)";
  view.innerHTML = `
    <div class="head fade-in"><div><h1>Trygghet</h1><p>Hur säker servern är, vad du delar – och vad som händer om du inte finns.</p></div></div>

    <div class="card sec-hero fade-in">
      <div class="ring big" style="--p:${sec.score};--c:${col}"><b>${sec.score}</b></div>
      <div class="grow"><h2 style="margin:0">🛡️ Säkerhetskoll</h2><p class="sec-grade">${esc(sec.grade)}</p>
        <p class="hint">${sec.bad ? `${sec.bad} sak${sec.bad > 1 ? "er" : ""} att fixa nu` : "Inget rött"}${sec.warn ? ` · ${sec.warn} att titta på` : ""} · kollat ${esc(timeAgo(sec.time))}</p></div>
      <button class="btn" id="secre">Kolla igen</button>
    </div>
    <div class="card list fade-in sec-list">${sec.items.map((i) => `<div class="row lvl-${i.level}"><div>
        <div class="n">${LEVEL[i.level]} ${esc(i.title)}</div><div class="m sec-text">${esc(i.text)}</div></div>
        <div class="acts">${i.link ? `<a class="btn" href="${esc(i.link)}">${esc(i.link_text || "Fixa")}</a>` : ""}</div></div>`).join("")}</div>

    <h2>🔗 Delningar <span class="count">${sh.shares.length}</span></h2>
    <p class="hint" style="margin:-8px 0 12px">Dela en fil eller mapp med en länk som slutar fungera av sig själv: <b>Filer</b> → <b>⋯</b> → <b>Dela länk</b>.</p>
    ${sh.shares.length ? `<div class="card list">${sh.shares.map((s) => `<div class="row"><div>
        <div class="n">${s.dir ? "📁" : "📄"} ${esc(s.name)} ${s.has_pin ? '<span class="badge">🔒 kod</span>' : ""} ${s.by === "Om jag försvinner" ? '<span class="badge">💌 brevet</span>' : ""}</div>
        <div class="m">${esc(s.path)} · ${esc(leftTxt(s.expires))} (${esc(fmtDay(s.expires))}) · nedladdad ${s.downloads} gång${s.downloads === 1 ? "" : "er"}</div></div>
        <div class="acts"><button class="btn" data-sl="${esc(s.token)}">Länk</button><button class="btn danger" data-ss="${esc(s.token)}">Stoppa</button></div></div>`).join("")}</div>`
      : `<div class="card empty-card"><span>Inget delas just nu.</span><a class="btn" href="#/files">Gå till Filer</a></div>`}

    <h2>💌 Om jag försvinner ${lg.enabled ? '<span class="badge ok">På</span>' : '<span class="badge">Av</span>'}</h2>
    <div class="card legacy fade-in" id="legacy"></div>`;
  $("#secre").onclick = async () => { $("#secre").disabled = true; $("#secre").textContent = "Kollar …"; try { await api("/api/security?fresh=1"); route(); } catch (e) { toast(e.message, "bad"); } };
  $$("[data-sl]").forEach((b) => b.onclick = () => showShareLink(sh.shares.find((s) => s.token === b.dataset.sl), base));
  $$("[data-ss]").forEach((b) => b.onclick = async () => {
    const s = sh.shares.find((x) => x.token === b.dataset.ss);
    if (!await confirmBox("Stoppa delningen?", `Länken till ${s.name} slutar fungera direkt. Filerna ligger kvar.`, "Stoppa", true)) return;
    try { await api(`/api/shares/${encodeURIComponent(s.token)}`, { method: "DELETE" }); toast("Delningen är stoppad", "good"); route(); } catch (e) { toast(e.message, "bad"); }
  });
  drawLegacy(lg);
};

function drawLegacy(lg) {
  const box = $("#legacy");
  const d = lg.deadlines;
  const days = (t) => Math.round((t - Date.now() / 1000) / 86400);
  const trustees = lg.trustees.length ? lg.trustees : [{ name: "", email: "" }];
  const baseGuess = lg.base_url || lg.effective_base || "";
  box.innerHTML = `
    <div class="lg-intro">
      <p>Om du inte har använt servern på <b>${lg.months} månader</b> och inte svarar på tre påminnelser, får de du litar på ett brev från dig –
        med din egen text och länkar till det du valt, t.ex. familjens foton och viktiga dokument.</p>
      <div class="lg-steps">
        <div><b>${lg.months} mån</b><span>utan aktivitet</span></div><i></i>
        <div><b>Påminnelse 1</b><span>e-post + mobil</span></div><i></i>
        <div><b>+7 dagar</b><span>påminnelse 2</span></div><i></i>
        <div><b>+14 dagar</b><span>sista påminnelsen</span></div><i></i>
        <div class="end"><b>+21 dagar</b><span>brevet skickas</span></div>
      </div>
      <p class="hint">Du räknas som "här" så fort du använder WizzOS, trycker på knappen i en påminnelse, eller skannar din QR-kod och anger din trygghetskod.</p>
    </div>
    ${lg.enabled ? `<div class="lg-status">✅ Senast här <b>${esc(timeAgo(lg.last_alive))}</b>${lg.last_source ? ` (${esc(lg.last_source)})` : ""}.
        Första påminnelsen kommer <b>${esc(fmtDay(d.remind1))}</b> (om ${days(d.remind1)} dagar) om du inte använder WizzOS innan dess.
        ${lg.released_at ? `<br>⚠️ Brevet skickades ${esc(fmtDay(lg.released_at))}.` : ""}
        <div class="actions" style="justify-content:flex-start;margin-top:10px"><button class="btn" id="lgqr">📱 Checka in med mobilen</button></div></div>` : ""}
    ${!lg.mail_ok ? `<div class="warnbar">Brevet och påminnelserna skickas med e-post. <a href="#/settings" style="color:inherit"><b>Ställ in e-post under Inställningar</b></a> först.</div>` : ""}
    <div class="grid2">
      <div><label for="lgn">Ditt namn</label><input id="lgn" value="${esc(lg.owner_name)}" placeholder="Anna"></div>
      <div><label for="lge">Din e-post (för påminnelser)</label><input id="lge" type="email" value="${esc(lg.owner_email)}" placeholder="du@exempel.se"></div>
      <div><label for="lgm">Hur länge utan aktivitet?</label><select id="lgm">${[1, 2, 3, 6, 12].map((m) => `<option value="${m}" ${m === lg.months ? "selected" : ""}>${m} månad${m > 1 ? "er" : ""}${m === 3 ? " (rekommenderas)" : ""}</option>`).join("")}</select></div>
      <div><label for="lgp">Trygghetskod (4–8 siffror)</label><input id="lgp" type="password" inputmode="numeric" maxlength="8" autocomplete="new-password" placeholder="${lg.has_pin ? "Sparad – lämna tomt för att behålla" : "Bara du ska kunna den"}"></div>
    </div>
    <label>Vem ska få brevet?</label>
    <div id="lgt">${trustees.map((t) => trusteeRow(t)).join("")}</div>
    <button class="btn" id="lgadd" type="button">+ Lägg till person</button>
    <label for="lgl">Ditt brev</label>
    <textarea id="lgl" rows="7" placeholder="Hej! Om du läser det här har något hänt mig. Här är våra bilder och de papper du kan behöva …">${esc(lg.letter)}</textarea>
    <label for="lgx">Praktiskt (valfritt)</label>
    <textarea id="lgx" rows="3" placeholder="Var lösenorden finns, vem som kan hjälpa till med datorn, vilka abonnemang som ska sägas upp …">${esc(lg.practical)}</textarea>
    <label>Mappar som ska delas med dem</label>
    <div id="lgf" class="chips">${lg.folders.map((f) => folderChip(f)).join("")}</div>
    <div class="row-in"><input id="lgfi" placeholder="/Media/Foton" list="lgdl"><datalist id="lgdl"></datalist><button class="btn" id="lgfa" type="button">Lägg till</button></div>
    <div class="grid2">
      <div><label for="lgd">Länkarna fungerar i</label><select id="lgd"><option value="30" ${lg.share_days === 30 ? "selected" : ""}>30 dagar</option><option value="90" ${lg.share_days === 90 ? "selected" : ""}>90 dagar</option></select></div>
      <div><label for="lgb">Adressen de når servern på</label><input id="lgb" value="${esc(baseGuess)}" placeholder="https://wizz.din-domän.se"></div>
    </div>
    ${!baseGuess || isPrivUrl(baseGuess) ? `<p class="hint">⚠️ ${baseGuess ? "Adressen är privat – mottagarna når den inte utanför ditt nätverk." : "Ingen adress än."} Ge WizzOS en egen adress med https (Inställningar → Egen adress) så att länkarna fungerar för dem.</p>` : ""}
    <label class="radio"><input type="checkbox" id="lgon" ${lg.enabled ? "checked" : ""}><div><b>Slå på "Om jag försvinner"</b><span>Du kan stänga av det när du vill. Inget skickas förrän efter ${lg.months} månader + tre obesvarade påminnelser.</span></div></label>
    <div class="actions">
      <button class="btn" id="lgtr">Provpåminnelse till mig</button>
      <button class="btn" id="lgtl">Provbrev till mig</button>
      <button class="btn" id="lgtell">Berätta för dem …</button>
      <button class="btn primary" id="lgsave">Spara</button>
    </div>
    ${lg.log.length ? `<details><summary>Händelser (${lg.log.length})</summary><div class="list" style="margin-top:8px">${lg.log.map((l) => `<div class="row"><div><div class="m">${esc(fmtDate(l.time))}</div><div>${esc(l.text)}</div></div></div>`).join("")}</div></details>` : ""}`;

  api("/api/files?path=/").then((r) => { $("#lgdl").innerHTML = (r.entries || r.items || []).filter((e) => e.dir).map((e) => `<option value="/${esc(e.name)}">`).join(""); }).catch(() => {});
  const bindRows = () => $$("#lgt [data-rm]").forEach((b) => b.onclick = () => { b.closest(".trow").remove(); });
  bindRows();
  $("#lgadd").onclick = () => { if ($$("#lgt .trow").length >= 6) return; $("#lgt").insertAdjacentHTML("beforeend", trusteeRow({ name: "", email: "" })); bindRows(); };
  const bindChips = () => $$("#lgf [data-rf]").forEach((b) => b.onclick = () => b.closest(".chip").remove());
  bindChips();
  $("#lgfa").onclick = () => { const v = $("#lgfi").value.trim(); if (!v) return; $("#lgf").insertAdjacentHTML("beforeend", folderChip(v)); $("#lgfi").value = ""; bindChips(); };
  const body = () => ({
    owner_name: $("#lgn").value, owner_email: $("#lge").value, months: +$("#lgm").value, pin: $("#lgp").value,
    trustees: $$("#lgt .trow").map((r) => ({ name: $(".tn", r).value, email: $(".te", r).value })),
    letter: $("#lgl").value, practical: $("#lgx").value, folders: $$("#lgf .chip").map((c) => c.dataset.f),
    share_days: +$("#lgd").value, base_url: $("#lgb").value, enabled: $("#lgon").checked,
  });
  const save = async (quiet) => { await api("/api/legacy", { method: "PUT", body: body() }); $("#lgp").value = ""; if (!quiet) toast("Sparat", "good"); };
  $("#lgsave").onclick = async () => { try { await save(); route(); } catch (e) { toast(e.message, "bad"); } };
  const test = (what) => async () => { try { const b = body(); b.enabled = undefined; await api("/api/legacy", { method: "PUT", body: b }); const r = await api("/api/legacy/test", { method: "POST", body: { what } }); toast(`Skickat till ${r.to}`, "good"); } catch (e) { toast(e.message, "bad"); } };
  $("#lgtr").onclick = test("reminder");
  $("#lgtl").onclick = test("letter");
  $("#lgtell").onclick = async () => {
    const names = body().trustees.map((t) => t.name || t.email).filter(Boolean).join(", ");
    if (!await confirmBox("Berätta för dem?", `${names} får ett kort mejl om att du valt dem som betrodda kontakter. De behöver inte göra något nu. Själva brevet skickas inte.`, "Skicka")) return;
    try { const b = body(); b.enabled = undefined; await api("/api/legacy", { method: "PUT", body: b }); const r = await api("/api/legacy/tell", { method: "POST" }); toast(`Mejl skickat till ${r.sent.join(", ")}`, "good"); } catch (e) { toast(e.message, "bad"); }
  };
  $("#lgqr")?.addEventListener("click", () => {
    if (!lg.qr_url) { toast("Ange adressen servern nås på först.", "bad"); return; }
    openModal(`<div class="phone"><div class="phone-qr"><img src="/api/qr?text=${encodeURIComponent(lg.qr_url)}" alt="QR-kod för incheckning" width="220" height="220"></div>
      <div><h3>📱 Checka in med mobilen</h3><p>Skanna koden och ange din trygghetskod – då vet WizzOS att du är här.</p>
      <p class="hint">Spara sidan på mobilens hemskärm, så räcker ett tryck och din kod när en påminnelse kommer. Koden skyddar så att ingen annan kan checka in åt dig.</p>
      ${isPrivUrl(lg.qr_url) ? `<p class="hint">🔒 Adressen är privat – mobilen behöver Tailscale eller vara hemma.</p>` : ""}</div></div>`);
  });
}

function trusteeRow(t) {
  return `<div class="trow"><input class="tn" placeholder="Namn" value="${esc(t.name)}"><input class="te" type="email" placeholder="e-post" value="${esc(t.email)}">
    <button class="icon-btn" data-rm type="button" aria-label="Ta bort"><svg><use href="#i-x"/></svg></button></div>`;
}
function folderChip(f) {
  return `<span class="chip" data-f="${esc(f)}">📁 ${esc(f)}<button type="button" data-rf aria-label="Ta bort">×</button></span>`;
}

/* ---------------- E-post (inställningar) ---------------- */

async function mailCard() {
  const box = $("#mailcard"); if (!box) return;
  const m = await api("/api/mail");
  box.innerHTML = `<h2>📧 E-post ${m.configured ? '<span class="badge ok">Inställd</span>' : '<span class="badge">Av</span>'}</h2>
    <p class="hint" style="margin-top:-6px">Behövs för "Om jag försvinner" (påminnelser och brevet). Gmail: server <b>smtp.gmail.com</b>, port 587, och ett
      <b>app-lösenord</b> (Google-konto → Säkerhet → Appösenord) – inte ditt vanliga lösenord.</p>
    <div class="grid2">
      <div><label for="mh">Server (SMTP)</label><input id="mh" value="${esc(m.host)}" placeholder="smtp.gmail.com"></div>
      <div><label for="mp">Port</label><input id="mp" type="number" value="${esc(m.port)}"></div>
      <div><label for="mu">Användarnamn</label><input id="mu" value="${esc(m.user)}" autocomplete="off" placeholder="du@gmail.com"></div>
      <div><label for="mw">Lösenord</label><input id="mw" type="password" autocomplete="new-password" placeholder="${m.has_password ? "Sparat – lämna tomt för att behålla" : "app-lösenord"}"></div>
      <div><label for="ms">Avsändare</label><input id="ms" value="${esc(m.sender)}" placeholder="du@gmail.com"></div>
      <div><label for="mx">Kryptering</label><select id="mx">${[["starttls", "STARTTLS (port 587)"], ["ssl", "SSL (port 465)"], ["none", "Ingen"]].map(([v, t]) => `<option value="${v}" ${m.security === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    </div>
    <div class="actions"><button class="btn" id="mtest">Skicka testmejl</button><button class="btn primary" id="msave">Spara</button></div>`;
  const body = () => ({ host: $("#mh").value, port: +$("#mp").value || 587, user: $("#mu").value, password: $("#mw").value, sender: $("#ms").value, security: $("#mx").value });
  $("#msave").onclick = async () => { try { await api("/api/mail", { method: "PUT", body: body() }); $("#mw").value = ""; toast("E-post sparad", "good"); mailCard(); } catch (e) { toast(e.message, "bad"); } };
  $("#mtest").onclick = async () => {
    const to = prompt("Skicka ett testmejl till:", $("#ms").value || "");
    if (!to) return;
    try { await api("/api/mail", { method: "PUT", body: body() }); $("#mw").value = ""; await api("/api/mail/test", { method: "POST", body: { to } }); toast(`Testmejl skickat till ${to}`, "good"); } catch (e) { toast(e.message, "bad"); }
  };
}

if (location.hash.startsWith("#/trygghet") && $("#shell") && !$("#shell").classList.contains("hidden")) route();
