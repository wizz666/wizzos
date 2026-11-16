/* WizzOS – familjekonton.
   Ägaren och admin ser allt som vanligt. Familj och gäster får en enkel
   startsida med "sina" appar, Familj även en egen mapp under Filer. */
"use strict";

const ROLE_NAMES = { admin: "Admin", family: "Familj", guest: "Gäst" };
const ROLE_HELP = {
  admin: "Får göra allt, precis som du.",
  family: "Ser apparna du väljer och har en egen mapp för sina filer.",
  guest: "Ser bara apparna du väljer. Inga filer.",
};
const EMOJIS = ["🙂", "😎", "🌸", "⚽", "🎮", "🎨", "🐶", "🐱", "🦊", "🚀", "🎵", "📚", "👵", "👴", "👧", "👦"];

function applyRole() {
  const me = window.ME || {};
  const limited = me.role !== "admin";
  document.body.classList.toggle("limited", limited);
  document.body.classList.toggle("no-files", limited && !me.files);
  $("#whoami").textContent = me.id && (window.WIZZ_PEOPLE || []).length ? `${me.emoji || ""} ${me.name}`.trim() : "";
  if (limited) { $("#ai-fab").classList.add("hidden"); $("#ai-panel").classList.add("hidden"); }
}

/* ---------- sidor för familj och gäster ---------- */

const limitedPages = {
  async home(view) {
    const me = window.ME;
    view.innerHTML = `
      <div class="head fade-in"><div><h1>${esc(greeting())}, ${esc(me.name)} ${esc(me.emoji || "")}</h1><p>Här är det du har tillgång till.</p></div></div>
      <div class="apps" id="myapps"><p class="hint">Laddar …</p></div>
      ${me.files ? `<h2 style="margin-top:26px">Dina filer</h2>
        <a class="card store-row fade-in" href="#/files" style="padding:16px;text-decoration:none;color:inherit"><svg style="width:28px;height:28px"><use href="#i-folder"/></svg>
          <div class="grow"><b>Min mapp</b><div class="m">Bara du (och den som sköter servern) ser det som ligger här.</div></div></a>` : ""}`;
    const d = await api("/api/me/apps");
    const box = $("#myapps");
    if (!d.apps.length) { box.innerHTML = `<p class="empty">Inga appar än. Be den som sköter servern att lägga till några åt dig.</p>`; return; }
    box.innerHTML = d.apps.map((a) => `
      <div class="card tile fade-in" data-name="${esc(a.name)}" tabindex="0">
        ${icon(a.icon, a.title)}
        <div class="t">${esc(a.title)}</div>
        <div class="st"><span class="dot ${esc(a.status)}"></span>${STATUS[a.status] || esc(a.status)}</div>
      </div>`).join("");
    $$("#myapps .tile").forEach((el) => {
      const a = d.apps.find((x) => x.name === el.dataset.name);
      el.onclick = () => openApp(a);
      el.onkeydown = (e) => { if (e.key === "Enter") openApp(a); };
    });
  },
  files(view) {
    if (!window.ME.files) { location.hash = "#/"; return; }
    return filesPage(view);
  },
  async me(view) {
    const me = window.ME;
    view.innerHTML = `
      <div class="head fade-in"><div><h1>Mitt konto</h1><p>${esc(me.emoji || "")} ${esc(me.name)} · ${esc(ROLE_NAMES[me.role] || me.role)}</p></div></div>
      <div class="settings"><div class="card"><h2>Byt lösenord</h2>
        <div class="grid2"><div><label for="mp0">Nuvarande</label><input type="password" id="mp0" autocomplete="current-password"></div>
        <div><label for="mp1">Nytt (minst 6 tecken)</label><input type="password" id="mp1" autocomplete="new-password"></div></div>
        <div class="actions"><button class="btn" id="mout">Logga ut</button><button class="btn primary" id="mpw">Byt lösenord</button></div>
      </div></div>`;
    $("#mpw").onclick = async () => {
      try {
        await api("/api/me/password", { method: "POST", body: { current: $("#mp0").value, password: $("#mp1").value } });
        $("#mp0").value = ""; $("#mp1").value = ""; toast("Lösenordet är bytt", "good");
      } catch (e) { toast(e.message, "bad"); }
    };
    $("#mout").onclick = logoutNow;
  },
};

async function logoutNow() {
  try { await api("/api/logout", { method: "POST" }); } catch (e) { /* redan utloggad */ }
  window.ME = null;
  try { const s = await api("/api/state"); window.WIZZ_PEOPLE = s.people || []; } catch (e) { /* ingen fara */ }
  showAuth(false);
}

/* ---------- kortet under Inställningar (ägare/admin) ---------- */

const fmtAgo = (t) => {
  if (!t) return "aldrig inloggad";
  const d = Math.floor((Date.now() / 1000 - t) / 86400);
  return d < 1 ? "inloggad i dag" : d === 1 ? "inloggad i går" : `inloggad för ${d} dagar sedan`;
};

async function familyCard() {
  const box = $("#famcard"); if (!box) return;
  const [u, a] = await Promise.all([api("/api/users"), api("/api/apps")]);
  const appsByName = Object.fromEntries(a.apps.map((x) => [x.name, x]));
  box.innerHTML = `<h2>👪 Familj och gäster</h2>
    <p class="hint" style="margin-top:-6px">Ge familj och vänner ett eget lösenord. De ser bara apparna du väljer – familjen får också en egen mapp under Filer (Hem/namn). Inställningar, backup och appbutiken är bara för dig och de du gör till admin.</p>
    ${u.users.map((p) => `<div class="store-row">
      <div class="who-ico">${esc(p.emoji || p.name.charAt(0))}</div>
      <div class="grow"><b>${esc(p.name)}</b> <span class="badge ${p.role === "admin" ? "ok" : ""}">${p.owner ? "Ägare" : esc(ROLE_NAMES[p.role] || p.role)}</span>
        <div class="m">${p.owner ? "Du – full åtkomst" : `${p.role === "admin" ? "Alla appar" : `${(p.apps || []).filter((n) => appsByName[n]).length} appar`}${p.folder ? ` · mapp ${esc(p.folder)}` : ""} · ${fmtAgo(p.last_login)}`}</div></div>
      <button class="btn" data-edit="${esc(p.id)}">Ändra</button></div>`).join("")}
    <div class="actions"><button class="btn primary" id="famadd"><svg><use href="#i-plus"/></svg>Lägg till person</button></div>`;
  $("#famadd").onclick = () => personModal(null, a.apps);
  $$("[data-edit]", box).forEach((b) => b.onclick = () => personModal(u.users.find((x) => x.id === b.dataset.edit), a.apps));
}

function personModal(p, apps) {
  const isNew = !p, owner = p && p.owner;
  let role = p ? p.role : "family", emoji = p ? (p.emoji || "") : EMOJIS[0];
  const chosen = new Set(p ? (p.apps || []) : []);
  const body = openModal(`<h3>${isNew ? "Lägg till person" : `Ändra ${esc(p.name)}`}</h3>
    <div class="grid2"><div><label for="pn">Namn</label><input id="pn" value="${esc(p ? p.name : "")}" maxlength="30" placeholder="T.ex. Anna"></div>
    <div><label for="ppw">${isNew ? "Lösenord (minst 6 tecken)" : "Nytt lösenord (lämna tomt)"}</label><input id="ppw" type="text" autocomplete="off" spellcheck="false" ${owner ? "disabled placeholder='Byts längre ner på sidan'" : ""}></div></div>
    <label>Symbol</label><div class="emoji-row" id="pem">${EMOJIS.map((e) => `<button type="button" class="${e === emoji ? "on" : ""}" data-e="${e}">${e}</button>`).join("")}</div>
    ${owner ? "" : `<label>Roll</label>${Object.keys(ROLE_NAMES).map((r) => `<label class="radio"><input type="radio" name="prole" value="${r}" ${r === role ? "checked" : ""}><div><b>${ROLE_NAMES[r]}</b><span>${ROLE_HELP[r]}</span></div></label>`).join("")}
    <div id="papps"><label style="margin-top:14px">Appar ${esc(p ? p.name : "personen")} ser</label>
      ${apps.length ? `<div class="app-checks">${apps.map((x) => `<label class="app-check"><input type="checkbox" value="${esc(x.name)}" ${chosen.has(x.name) ? "checked" : ""}>${icon(x.icon, x.title)}<span>${esc(x.title)}</span></label>`).join("")}</div>`
        : `<p class="hint">Du har inga appar installerade än.</p>`}
      <p class="hint">Personen loggar in i WizzOS och ser apparna som knappar. Själva appen kan ha egen inloggning – skapa ett konto åt personen där också.</p></div>`}
    <p class="error" id="perr"></p>
    <div class="actions">${isNew || owner ? "" : `<button class="btn danger" id="pdel" style="margin-right:auto">Ta bort</button>`}
      <button class="btn" data-close>Avbryt</button><button class="btn primary" id="pok">${isNew ? "Lägg till" : "Spara"}</button></div>`);
  const showApps = () => { const el = $("#papps", body); if (el) el.classList.toggle("hidden", role === "admin"); };
  showApps();
  $$("[name=prole]", body).forEach((r) => r.onchange = () => { role = r.value; showApps(); });
  $$("#pem [data-e]", body).forEach((b) => b.onclick = () => { emoji = b.dataset.e; $$("#pem [data-e]", body).forEach((x) => x.classList.toggle("on", x === b)); });
  $("#pok", body).onclick = async () => {
    const data = { name: $("#pn", body).value.trim(), emoji };
    if (!owner) {
      data.role = role;
      data.apps = $$(".app-checks input:checked", body).map((i) => i.value);
      const pw = $("#ppw", body).value;
      if (pw) data.password = pw;
    }
    try {
      if (isNew) await api("/api/users", { method: "POST", body: data });
      else await api(`/api/users/${encodeURIComponent(p.id)}`, { method: "PUT", body: data });
      closeModal();
      toast(isNew ? `${data.name} är tillagd` : "Sparat", "good");
      if (isNew && data.password) toast(`Ge ${data.name} lösenordet – det visas inte igen.`);
      refreshPeople(); familyCard();
    } catch (e) { $("#perr", body).textContent = e.message; }
  };
  const del = $("#pdel", body);
  if (del) del.onclick = async () => {
    closeModal();
    const ok = await confirmBox(`Ta bort ${p.name}?`, `${p.name} kan inte logga in längre.${p.folder ? ` Mappen ${p.folder} ligger kvar – radera den själv under Filer om du vill.` : ""}`, "Ta bort", true);
    if (!ok) return;
    try { await api(`/api/users/${encodeURIComponent(p.id)}`, { method: "DELETE" }); toast(`${p.name} är borttagen`, "good"); refreshPeople(); familyCard(); }
    catch (e) { toast(e.message, "bad"); }
  };
}

async function refreshPeople() {
  try { const s = await api("/api/state"); window.WIZZ_PEOPLE = s.people || []; applyRole(); } catch (e) { /* ingen fara */ }
}
