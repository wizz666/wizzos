/* WizzOS – kortet för Telegram under Inställningar. */
"use strict";

async function telegramCard() {
  const box = $("#tgcard"); if (!box) return;
  const t = await api("/api/telegram");
  const linked = t.chats.length;
  box.innerHTML = `<h2>💬 Telegram ${linked ? '<span class="badge ok">Kopplad</span>' : ""}</h2>
    <p class="hint" style="margin-top:-6px">Prata med din server från mobilen: fråga hur den mår, be AI-hjälpen förklara något, och få larm och agentens frågor
      med knappar – godkänn eller neka direkt i chatten. Bara chattar du själv kopplat får svar.</p>
    ${!t.configured ? `<ol class="hint" style="margin:.2rem 0 .8rem 1.1rem;line-height:1.6">
        <li>Öppna Telegram och skriv till <b>@BotFather</b>.</li>
        <li>Skicka <code>/newbot</code>, välj ett namn och ett användarnamn som slutar på <code>bot</code>.</li>
        <li>BotFather svarar med en <b>nyckel</b> (siffror:bokstäver). Klistra in den här.</li></ol>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><input id="tgtok" type="password" autocomplete="off" placeholder="123456789:ABC…" style="max-width:340px"><button class="btn primary" id="tgsave">Spara</button></div>`
    : `<p>Boten: <b>@${esc(t.bot)}</b></p>
      ${linked ? `<div class="card list">${t.chats.map((c) => `<div class="row"><div><div class="n">📱 ${esc(c.name || "Telegram")}</div></div>
        <div class="acts"><button class="btn" data-tgun="${c.id}">Koppla bort</button></div></div>`).join("")}</div>` : ""}
      ${t.pair_code ? `<p style="margin-top:10px">Skicka den här koden till <b>@${esc(t.bot)}</b> i Telegram (gäller 10 minuter):</p><p style="font-size:28px;font-weight:800;letter-spacing:4px">${esc(t.pair_code)}</p>`
        : `<div class="actions" style="justify-content:flex-start"><button class="btn${linked ? "" : " primary"}" id="tgpair">${linked ? "Koppla en till telefon" : "Koppla min Telegram"}</button></div>`}
      <label class="radio"><input type="checkbox" id="tgal" ${t.alerts ? "checked" : ""}><div><b>Skicka larm hit</b><span>Larm (när Larm är påslaget), veckobrevet och agentens frågor.</span></div></label>
      <p class="hint">I chatten: <code>/status</code>, <code>/appar</code>, <code>/backup</code> – allt annat svarar AI-hjälpen på.</p>
      <div class="actions"><button class="btn" id="tgoff">Ta bort boten</button></div>`}`;
  const re = () => telegramCard().catch(() => {});
  $("#tgsave")?.addEventListener("click", async () => { try { await api("/api/telegram", { method: "PUT", body: { token: $("#tgtok").value } }); toast("Boten är sparad – koppla nu din Telegram", "good"); re(); } catch (e) { toast(e.message, "bad"); } });
  $("#tgpair")?.addEventListener("click", async () => { try { await api("/api/telegram/pair", { method: "POST" }); re(); setTimeout(re, 20000); setTimeout(re, 45000); } catch (e) { toast(e.message, "bad"); } });
  $("#tgal")?.addEventListener("change", async (e) => { try { await api("/api/telegram/alerts", { method: "PUT", body: { on: e.target.checked } }); toast("Sparat", "good"); } catch (err) { toast(err.message, "bad"); } });
  $("#tgoff")?.addEventListener("click", async () => { if (!confirm("Ta bort boten från WizzOS? Alla kopplade chattar slutar få svar.")) return; await api("/api/telegram", { method: "DELETE" }); re(); });
  box.querySelectorAll("[data-tgun]").forEach((b) => b.onclick = async () => { await api(`/api/telegram/chats/${b.dataset.tgun}`, { method: "DELETE" }); re(); });
}
