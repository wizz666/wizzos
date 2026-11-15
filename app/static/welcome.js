/* WizzOS – välkomstguide vid första start + kortet "WizzOS-version" (självuppdatering). */
"use strict";

/* ---------------- Välkomstguiden ---------------- */

const WSTEPS = [
  { id: "name", title: "Döp din server", emo: "🏷️" },
  { id: "alerts", title: "Larm till mobilen", emo: "🔔" },
  { id: "backup", title: "Backup", emo: "🛡️" },
  { id: "goal", title: "Vad vill du göra?", emo: "🎯" },
];

pages.welcome = async function (view) {
  const [s, al] = await Promise.all([api("/api/settings"), api("/api/alerts")]);
  let i = 0;
  const draw = () => {
    const st = WSTEPS[i];
    view.innerHTML = `<div class="welcome fade-in">
      <div class="wl-top"><img src="/static/logo-192.png" alt=""><div><h1>Välkommen till WizzOS</h1><p>Fyra snabba steg – sedan är din server trygg och redo. Allt går att ändra senare.</p></div></div>
      <div class="wl-steps">${WSTEPS.map((x, n) => `<div class="${n < i ? "done" : n === i ? "on" : ""}"><span>${n < i ? "✓" : x.emo}</span>${esc(x.title)}</div>`).join("")}</div>
      <div class="card wl-card" id="wlc"></div>
      <div class="wl-nav"><button class="btn" id="wlskipall">Hoppa över guiden</button></div>
    </div>`;
    $("#wlskipall").onclick = finish;
    const box = $("#wlc");
    const next = () => { i++; if (i >= WSTEPS.length) finish(); else draw(); };
    const nav = (primary, onPrimary) => `<div class="actions"><button class="btn" id="wlskip">Hoppa över</button><button class="btn primary" id="wlnext">${primary}</button></div>`;
    if (st.id === "name") {
      box.innerHTML = `<h2>🏷️ Vad ska servern heta?</h2><p class="hint">Namnet syns överst, i larm och i veckobrevet. T.ex. "Hemma" eller "Familjens server".</p>
        <div class="grid2"><div><label for="wln">Namn</label><input id="wln" value="${esc(s.server_name)}" placeholder="Hemma"></div>
        <div><label for="wlt">Tidszon</label><input id="wlt" value="${esc(s.tz)}"></div></div>${nav("Nästa")}`;
      $("#wlnext").onclick = async () => { try { await api("/api/settings", { method: "PUT", body: { server_name: $("#wln").value.trim(), tz: $("#wlt").value.trim() } }); next(); } catch (e) { toast(e.message, "bad"); } };
    } else if (st.id === "alerts") {
      const has = al.enabled && al.ntfy_topic;
      box.innerHTML = `<h2>🔔 Få veta när något händer</h2>
        <p>WizzOS skickar en notis till mobilen om något går fel – och när det är löst. Den kommer också med frågor från AI:n och ett kort veckobrev.</p>
        ${has ? `<div class="lg-status">✅ Larm är redan påslagna (${esc(al.ntfy_url)} · ${esc(al.ntfy_topic)}).</div>${nav("Nästa")}` : `
        <ol class="goal-steps"><li>Installera appen <b>ntfy</b> i mobilen (gratis): <a href="https://apps.apple.com/app/ntfy/id1625396347" target="_blank" rel="noopener">iPhone</a> · <a href="https://play.google.com/store/apps/details?id=io.heckel.ntfy" target="_blank" rel="noopener">Android</a></li>
          <li>Tryck på knappen nedan – WizzOS skapar en egen, hemlig kanal åt dig.</li><li>Skanna QR-koden med mobilen (eller tryck + i ntfy-appen och skriv kanalnamnet).</li></ol>
        <div id="wlntfy"></div>
        <div class="actions"><button class="btn" id="wlskip">Hoppa över</button><button class="btn primary" id="wlmk">Skapa min larmkanal</button></div>`}`;
      if (has) $("#wlnext").onclick = next;
      $("#wlmk")?.addEventListener("click", async () => {
        const rnd = Array.from(crypto.getRandomValues(new Uint8Array(9)), (b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");
        const topic = `wizzos-${rnd}`;
        try {
          await api("/api/alerts", { method: "PUT", body: { enabled: true, ntfy_url: "https://ntfy.sh", ntfy_topic: topic } });
          const url = `https://ntfy.sh/${topic}`;
          $("#wlntfy").innerHTML = `<div class="phone"><div class="phone-qr"><img src="/api/qr?text=${encodeURIComponent(url)}" alt="QR-kod till larmkanalen" width="170" height="170"></div>
            <div><b>Din larmkanal</b><p class="mono-url">${esc(topic)}</p><p class="hint">Kanalnamnet är hemligt – den som känner till det kan läsa larmen. Dela det inte.</p>
            <button class="btn" id="wltest">Skicka ett testlarm</button></div></div>`;
          $("#wltest").onclick = async () => { try { await api("/api/alerts/test", { method: "POST" }); toast("Testlarmet är skickat – kolla mobilen", "good"); } catch (e) { toast(e.message, "bad"); } };
          $("#wlmk").textContent = "Nästa"; $("#wlmk").onclick = next;
        } catch (e) { toast(e.message, "bad"); }
      });
    } else if (st.id === "backup") {
      box.innerHTML = `<h2>🛡️ Backup – det viktigaste steget</h2>
        <p>Varje natt sparas dina appar krypterat på ett annat ställe (en annan server, en USB-disk eller molnet). WizzOS <b>provar dessutom att återställa</b> backupen – så du vet att den fungerar den dag du behöver den.</p>
        <p class="hint">Det tar ett par minuter. Du kan också göra det senare under Backup.</p>
        <div class="actions"><button class="btn" id="wlskip">Senare</button><button class="btn primary" id="wlnext">Ställ in backup nu</button></div>`;
      $("#wlnext").onclick = async () => { await finish(false); location.hash = "#/backup"; };
    } else if (st.id === "goal") {
      box.innerHTML = `<h2>🎯 Vad vill du göra med servern?</h2><p class="hint">Välj ett mål så installerar WizzOS rätt app och visar hur du kommer igång. Du kan prova i en timme först.</p>
        <div class="head" style="display:none"></div><div id="wlgoals"></div>
        <div class="actions"><button class="btn primary" id="wlnext">Klar – till startsidan</button></div>`;
      drawGoals(box).then(() => { const g = $("#goals", box); if (g) { $("h2", g)?.remove(); } });
      $("#wlnext").onclick = () => finish();
    }
    $("#wlskip")?.addEventListener("click", next);
  };
  const finish = async (go = true) => {
    try { await api("/api/welcome/done", { method: "POST" }); } catch { /* */ }
    if (go) { location.hash = "#/"; toast("Klart! Din server är redo 🎉", "good"); }
  };
  draw();
};

/* Visa guiden automatiskt efter att lösenordet valts första gången */
(async () => {
  for (let n = 0; n < 40 && $("#shell")?.classList.contains("hidden"); n++) await new Promise((r) => setTimeout(r, 250));
  try { const w = await api("/api/welcome"); if (w.pending && (location.hash === "" || location.hash === "#/")) location.hash = "#/welcome"; } catch { /* */ }
})();

/* ---------------- Kortet "WizzOS-version" ---------------- */

async function selfUpdCard(force = false) {
  const box = $("#selfupd"); if (!box) return;
  const s = await api(`/api/selfupdate${force ? "?force=true" : ""}`);
  const lt = s.latest || {};
  const last = s.last || {};
  const lastTxt = { done: `✅ Uppdaterad ${esc(last.from)} → ${esc(last.to)}`, rolled_back: `↶ ${esc(last.to)} backades – kör ${esc(last.from)}`,
    failed: `⚠️ Uppdateringen till ${esc(last.to)} kunde inte göras`, running: `⏳ Uppdaterar till ${esc(last.to)} …` }[last.state] || "";
  box.innerHTML = `<h2>⬆️ WizzOS ${esc(s.current)} ${s.available ? `<span class="badge warnb">${esc(lt.version)} finns</span>` : lt.version ? '<span class="badge ok">Senaste</span>' : ""}</h2>
    <p class="hint" style="margin-top:-6px">WizzOS uppdaterar sig själv på natten, efter apparna. Startar den nya versionen inte som den ska backar den automatiskt till den gamla.</p>
    ${!s.managed ? `<div class="warnbar" style="background:var(--card-hi);border-color:var(--line)">Den här servern kör en utvecklarversion (byggd från källkoden${s.image_tag ? ": " + esc(s.image_tag) : ""}) och uppdateras inte automatiskt.</div>` : ""}
    ${lt.error ? `<p class="hint">${esc(lt.error)}</p>` : ""}
    ${lastTxt ? `<p>${lastTxt}${last.time ? ` <span class="hint">(${esc(timeAgo(last.time))})</span>` : ""}</p>` : ""}
    ${s.available && lt.notes ? `<details open><summary>Nytt i ${esc(lt.version)}</summary><div class="rel-notes">${md(lt.notes)}</div></details>` : ""}
    <label class="radio"><input type="checkbox" id="suauto" ${s.auto ? "checked" : ""} ${s.managed ? "" : "disabled"}><div><b>Uppdatera WizzOS automatiskt</b><span>På natten, tillsammans med apparna. Rekommenderas.</span></div></label>
    <div class="actions"><button class="btn" id="sucheck">Leta efter ny version</button>${s.available && s.managed && !s.running ? '<button class="btn primary" id="sugo">Uppdatera nu</button>' : ""}</div>`;
  $("#sucheck").onclick = () => selfUpdCard(true);
  $("#suauto").onchange = async (e) => { try { await api("/api/selfupdate", { method: "PUT", body: { auto: e.target.checked } }); toast("Sparat", "good"); } catch (er) { toast(er.message, "bad"); } };
  $("#sugo")?.addEventListener("click", async () => {
    if (!await confirmBox(`Uppdatera till ${lt.version}?`, "WizzOS startar om en kort stund (dina appar påverkas inte). Fungerar den nya versionen inte backar den automatiskt.", "Uppdatera")) return;
    try {
      await api("/api/selfupdate/run", { method: "POST" });
      toast("Uppdaterar … sidan laddas om när WizzOS är tillbaka", "good");
      const want = lt.version; const t0 = Date.now();
      const poll = setInterval(async () => {
        try { const st = await (await fetch("/api/state")).json(); if (st.version === want || Date.now() - t0 > 180000) { clearInterval(poll); location.reload(); } } catch { /* startar om */ }
      }, 3000);
    } catch (e) { toast(e.message, "bad"); }
  });
}
