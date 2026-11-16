/* WizzOS – AI-koppling (MCP): nycklar, kopplingsinstruktioner och AI-dagbok.
   Laddas efter app.js och trygghet.js. */
"use strict";

/* Frågor från AI:n som väntar på ditt svar – överst på startsidan. */
let apprSig = "";
async function drawApprovals() {
  const box = $("#approvals"); if (!box) return;
  let d; try { d = await api("/api/approvals"); } catch { return; }
  const sig = d.pending.map((r) => r.id).join(",");
  if (sig === apprSig && box.childElementCount === (d.pending.length ? 1 : 0)) {
    $$("#approvals [data-left]").forEach((el) => { el.textContent = Math.max(0, Math.ceil((+el.dataset.left - Date.now() / 1000) / 60)); });
    return;
  }
  apprSig = sig;
  box.innerHTML = d.pending.length ? `<div class="card appr fade-in">${d.pending.map((r) => `<div class="appr-row">
      <div class="appr-ico">🤖</div>
      <div class="grow"><div class="m">${esc(r.who)} vill</div><b>${esc(r.title)}</b><p>${esc(r.detail)}</p>
        <div class="m">Svara inom <span data-left="${r.expires}">${Math.max(0, Math.ceil((r.expires - Date.now() / 1000) / 60))}</span> min – annars händer ingenting.</div></div>
      <div class="acts"><button class="btn primary" data-ay="${esc(r.id)}">✅ Godkänn</button><button class="btn" data-an="${esc(r.id)}">⛔ Neka</button></div></div>`).join("")}</div>` : "";
  const act = (id, yes) => async () => {
    try { await api(`/api/approvals/${encodeURIComponent(id)}/${yes ? "yes" : "no"}`, { method: "POST" });
      toast(yes ? "Godkänt – det görs nu" : "Nekat – ingenting görs", "good"); apprSig = "x"; drawApprovals(); } catch (e) { toast(e.message, "bad"); apprSig = "x"; drawApprovals(); }
  };
  $$("#approvals [data-ay]").forEach((b) => b.onclick = act(b.dataset.ay, true));
  $$("#approvals [data-an]").forEach((b) => b.onclick = act(b.dataset.an, false));
}

/* Strömbrytaren: Av = AI-appar ser servern som otillgänglig och laddar inga verktyg. */
async function aiSwitch(btn, known) {
  if (!btn) return;
  let on = known;
  if (on === undefined) { try { on = (await api("/api/ai-keys")).enabled; } catch { return; } }
  const draw = () => { btn.classList.remove("hidden"); btn.classList.toggle("on", on); btn.setAttribute("aria-checked", String(on));
    btn.title = on ? "AI-kopplingen är på – klicka för att stänga av" : "AI-kopplingen är av – klicka för att slå på"; };
  draw();
  btn.onclick = async () => {
    try {
      on = (await api("/api/ai-link", { method: "PUT", body: { enabled: !on } })).enabled; draw();
      toast(on ? "🤖 AI-kopplingen är på. Starta en ny Claude-session för att använda den."
        : "🤖 AI-kopplingen är av. AI-appar ser inte servern och laddar inga verktyg – det sparar krediter.", "good");
      if ($("#ailink")) aiLinkCard();
    } catch (e) { toast(e.message, "bad"); }
  };
}

async function aiLinkCard() {
  const box = $("#ailink"); if (!box) return;
  const d = await api("/api/ai-keys");
  const url = (d.base || location.origin).replace(/\/$/, "") + "/mcp";
  if (!d.base) api("/api/phone-base", { method: "PUT", body: { base: location.origin } }).catch(() => {});
  const exp = (k) => k.expires ? (k.expires < Date.now() / 1000 ? "har gått ut" : `gäller till ${fmtDay(k.expires)}`) : "gäller tills du stänger av den";
  box.innerHTML = `<h2 class="h-sw">🤖 AI-koppling <button class="qa ai-sw" id="ai-sw2" type="button" role="switch"><b>${d.enabled ? "På" : "Av"}</b><i class="sw"><i></i></i></button></h2>
    ${d.enabled ? "" : `<div class="warnbar" style="background:var(--card-hi);border-color:var(--line)">AI-kopplingen är <b>avstängd</b>. Inga AI-appar kan nå servern, och de laddar inga verktyg – det sparar krediter. Nycklarna finns kvar.</div>`}
    <p class="hint" style="margin-top:-6px">Låt Claude, ChatGPT eller andra AI-appar sköta servern åt dig – "installera något för familjens foton", "varför är servern seg?".
      AI:n får samma skyddsnät som du: nya appar installeras på prov, uppdateringar backas vid fel och allt skrivs i AI-dagboken. Den kan aldrig radera, återställa, ändra inställningar eller se lösenord.</p>
    <div class="ai-levels"><div><b>👀 Bara titta</b><span>Status, appar, loggar, backup, säkerhet, kostnad och mapplistor.</span></div>
      <div><b>🛠️ Vanliga saker</b><span>Dessutom installera på prov, starta/stoppa, uppdatera säkert, spara återställningspunkt, köra backup och skapa delningslänkar.</span></div>
      <div><b>🔐 Allt – du godkänner</b><span>Dessutom ta bort appar, återställa, installera permanent m.m. Varje sådan sak blir en fråga i mobilen med Godkänn/Neka – inget händer utan ditt ja.</span></div></div>
    ${d.keys.length ? `<div class="list" style="margin-top:14px">${d.keys.map((k) => `<div class="row"><div>
        <div class="n">${k.web ? "🌐" : "🔑"} ${esc(k.name)} <span class="badge ${k.level !== "read" ? "ok" : ""}">${esc(k.level_text)}</span></div>
        <div class="m">Skapad ${esc(fmtDay(k.created))} · ${esc(exp(k))} · ${k.last_used ? `senast använd ${esc(timeAgo(k.last_used))}, ${k.uses} anrop` : "inte använd än"}</div></div>
        <div class="acts"><button class="btn danger" data-kr="${esc(k.id)}">Stäng av</button></div></div>`).join("")}</div>` : ""}
    <div class="actions" style="justify-content:flex-start"><button class="btn primary" id="aikadd">+ Koppla en AI</button><button class="btn" id="aiweb">🌐 Claude/ChatGPT i mobilen</button></div>
    ${d.diary.length ? `<details><summary>📓 AI-dagbok (${d.diary.length})</summary><div class="list" style="margin-top:8px">${d.diary.map((e) => `<div class="row"><div>
        <div class="n">${e.ok ? "✅" : "⛔"} <span>${esc(e.text)}</span></div>
        <div class="m">${esc(fmtDate(e.time))} · ${esc(e.who)}${e.tool !== "—" ? ` · ${esc(e.tool)}` : ""}${Object.keys(e.args || {}).length ? ` · ${esc(Object.entries(e.args).map(([k, v]) => `${k}=${v}`).join(", "))}` : ""}</div></div></div>`).join("")}</div></details>` : ""}`;
  aiSwitch($("#ai-sw2"), d.enabled);
  $$("#ailink [data-kr]").forEach((b) => b.onclick = async () => {
    const k = d.keys.find((x) => x.id === b.dataset.kr);
    if (!await confirmBox("Stäng av nyckeln?", `${k.name} kan inte längre nå WizzOS. Det går inte att ångra – skapa en ny nyckel om du vill koppla igen.`, "Stäng av", true)) return;
    try { await api(`/api/ai-keys/${encodeURIComponent(k.id)}`, { method: "DELETE" }); toast("Nyckeln är avstängd", "good"); aiLinkCard(); } catch (e) { toast(e.message, "bad"); }
  });
  $("#aiweb").onclick = () => {
    const url = d.public ? d.public.replace(/\/$/, "") + "/mcp" : (d.base || location.origin).replace(/\/$/, "") + "/mcp";
    const priv = isPrivUrl(url);
    openModal(`<h3>🌐 Claude eller ChatGPT i mobilen och på webben</h3>
      <p>Här behövs ingen nyckel. Du lägger till adressen i AI-appen, loggar in med ditt WizzOS-lösenord och väljer vad den får göra.</p>
      <label>Adress att lägga in</label><div class="copyline"><code>${esc(url)}</code><button class="btn" data-cp="${esc(url)}">Kopiera</button></div>
      <div class="howto" style="margin-top:14px"><div><b>Claude</b><span>claude.ai → <b>Inställningar → Connectors → Add custom connector</b>. Klistra in adressen och tryck <b>Connect</b>. Kopplingen följer med till mobilappen.</span></div>
        <div><b>ChatGPT</b><span>Inställningar → <b>Connectors</b> (utvecklarläge) → <b>Skapa</b>, klistra in adressen, välj OAuth.</span></div></div>
      <p class="hint">En WizzOS-sida öppnas: ange lösenordet, välj nivå och hur länge – tryck Godkänn. Kopplingen dyker upp här med 🌐 och kan stängas av när som helst.</p>
      ${d.public ? `<p class="hint">🔒 Den här adressen släpper bara igenom AI-kopplingen, inloggningen och godkännandeknapparna. Panelen och dina filer nås fortfarande bara via Tailscale.</p>` : ""}
      ${priv ? `<div class="warnbar">⚠️ Adressen nås bara från dina egna enheter (Tailscale). Claude och ChatGPT kör i molnet och måste kunna nå <b>/mcp</b> och inloggningssidan från internet – annars går det inte att koppla.</div>` : ""}
      <div class="actions"><button class="btn primary" data-close>Klar</button></div>`);
    $$("#modal [data-cp]").forEach((b) => b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.cp); toast("Kopierat", "good"); } catch { toast(url); } });
  };
  $("#aikadd").onclick = () => {
    openModal(`<h3>🤖 Koppla en AI</h3>
      <label for="akn">Vad ska nyckeln heta?</label><input id="akn" maxlength="40" placeholder="Claude på jobbdatorn">
      <label>Vad får den göra?</label>
      <label class="radio"><input type="radio" name="akl" value="read" checked><div><b>👀 Bara titta</b><span>Bra att börja med.</span></div></label>
      <label class="radio"><input type="radio" name="akl" value="act"><div><b>🛠️ Vanliga saker</b><span>Installera på prov, starta om, uppdatera säkert, backup, delningslänkar.</span></div></label>
      <label class="radio"><input type="radio" name="akl" value="all"><div><b>🔐 Allt – du godkänner</b><span>Även ta bort, återställa och installera permanent – men bara efter ditt ja i mobilen eller här. Frågan skickas via larmkanalen (ntfy).</span></div></label>
      <label for="akd">Hur länge ska den gälla?</label>
      <select id="akd"><option value="30">30 dagar</option><option value="90" selected>90 dagar</option><option value="365">1 år</option><option value="0">Tills jag stänger av den</option></select>
      <div class="actions"><button class="btn" data-close>Avbryt</button><button class="btn primary" id="akgo">Skapa nyckel</button></div>`);
    $("#akgo").onclick = async () => {
      try {
        const k = await api("/api/ai-keys", { method: "POST", body: { name: $("#akn").value || "AI", level: $("input[name=akl]:checked").value, days: +$("#akd").value } });
        aiKeyShow(k, url); aiLinkCard();
      } catch (e) { toast(e.message, "bad"); }
    };
  };
}

function aiKeyShow(k, url) {
  const code = `claude mcp add --transport http wizzos ${url} --header "Authorization: Bearer ${k.token}"`;
  const desk = JSON.stringify({ mcpServers: { wizzos: { command: "npx", args: ["-y", "mcp-remote", url, "--header", "Authorization:${WIZZOS_AUTH}"], env: { WIZZOS_AUTH: `Bearer ${k.token}` } } } }, null, 2);
  openModal(`<h3>🔑 ${esc(k.name)} är klar</h3>
    <div class="warnbar">Nyckeln visas bara nu. Kopiera den till din AI-app – WizzOS sparar bara ett fingeravtryck av den.</div>
    <label>Adress</label><div class="copyline"><code>${esc(url)}</code><button class="btn" data-cp="${esc(url)}">Kopiera</button></div>
    <label>Nyckel</label><div class="copyline"><code>${esc(k.token)}</code><button class="btn" data-cp="${esc(k.token)}">Kopiera</button></div>
    <details open><summary><b>Claude Code</b> (terminalen)</summary><div class="copyline"><code>${esc(code)}</code><button class="btn" data-cp="${esc(code)}">Kopiera</button></div></details>
    <details><summary><b>Claude Desktop</b> (claude_desktop_config.json)</summary><pre class="cfg">${esc(desk)}</pre><button class="btn" data-cp="${esc(desk)}">Kopiera</button>
      <p class="hint">Kräver Node.js. Starta om Claude Desktop efteråt.</p></details>
    <details><summary><b>Andra AI-appar</b></summary><p class="hint">Välj "MCP-server via HTTP" (streamable HTTP), ange adressen ovan och lägg till rubriken <code>Authorization: Bearer &lt;nyckeln&gt;</code>.
      Claude och ChatGPT i mobilen och på webben kräver inloggning via webben – det kommer i nästa steg.</p></details>
    ${isPrivUrl(url) ? `<p class="hint">🔒 Adressen är privat – datorn med AI-appen behöver vara på samma nätverk eller ha Tailscale.</p>` : ""}
    <div class="actions"><button class="btn primary" data-close>Klar</button></div>`);
  $$("#modal [data-cp]").forEach((b) => b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.cp); toast("Kopierat", "good"); } catch { toast("Markera och kopiera texten", "bad"); } });
}
