/* WizzOS – kortet för WizzOS-agenten under Inställningar. */
"use strict";

const AGENT_OUT = { "löst": "✅", "väntar på dig": "🔔", "förklarat": "💬", "hoppade över": "·", "fel": "⚠️", "prov": "🧪" };

async function agentCard() {
  const box = $("#agentcard"); if (!box) return;
  const a = await api("/api/agent");
  box.innerHTML = `<h2>🤖 WizzOS-agenten ${a.enabled ? '<span class="badge ok">På</span>' : ""}</h2>
    <p class="hint" style="margin-top:-6px">Agenten vaknar bara när något går fel – en app stannar, backupen misslyckas, disken fylls eller certifikatet inte förnyas.
      Den provar först det enkla själv och använder AI bara när det behövs för att förklara. Allt som är svårt att ångra blir en fråga i mobilen.</p>
    <label class="radio"><input type="checkbox" id="agon" ${a.enabled ? "checked" : ""}><div><b>Slå på agenten</b><span>Den kostar inget när allt fungerar.</span></div></label>
    <label class="radio"><input type="radio" name="aglvl" value="fix" ${a.level === "fix" ? "checked" : ""}><div><b>Fixa det enkla själv</b><span>Startar om en app en gång, kör backupen igen, läser in proxyn på nytt. Rekommenderas.</span></div></label>
    <label class="radio"><input type="radio" name="aglvl" value="suggest" ${a.level === "suggest" ? "checked" : ""}><div><b>Bara förklara och föreslå</b><span>Gör ingenting själv.</span></div></label>
    <div class="grid2"><div><label for="agmax">Högst antal AI-anrop per dag</label><input id="agmax" type="number" min="0" max="200" value="${a.ai_daily_max}"></div>
      <div><label>&nbsp;</label><p class="hint" style="margin:8px 0 0">${a.ai_ready ? `I dag: ${a.ai_today} anrop.` : "AI-hjälpen är inte inställd – agenten fixar det enkla men förklarar inte."}</p></div></div>
    <label for="agnotes">Anteckningar till agenten</label>
    <textarea id="agnotes" rows="5" spellcheck="false" translate="no" placeholder="- Jellyfin får vara avstängd på nätterna.">${esc(a.notes)}</textarea>
    <p class="hint">Sådant agenten ska känna till, en sak per rad. Lösenord och nycklar hör inte hemma här.</p>
    <div class="actions"><button class="btn" id="agtest">🧪 Prova agenten</button><button class="btn primary" id="agsave">Spara</button></div>
    ${a.runs.length ? `<details><summary>Vad agenten har gjort (${a.runs.length})</summary><div class="list" style="margin-top:8px">${a.runs.map((r) => `
      <div class="row"><div><div class="n">${AGENT_OUT[r.outcome] || "·"} <span>${esc(r.title)}</span> <span class="badge">${esc(r.outcome)}</span></div>
      <div class="m">${esc(fmtDate(r.time))}${r.ai_calls ? ` · ${r.ai_calls} AI` : ""}</div>
      ${r.steps.length ? `<ul class="hint" style="margin:4px 0 0 18px">${r.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}</div></div>`).join("")}</div></details>` : ""}`;
  $("#agsave").onclick = async () => {
    try {
      await api("/api/agent", { method: "PUT", body: { enabled: $("#agon").checked, level: $("[name=aglvl]:checked").value,
        ai_daily_max: Number($("#agmax").value || 0), notes: $("#agnotes").value } });
      toast("Agenten är sparad", "good"); agentCard();
    } catch (e) { toast(e.message, "bad"); }
  };
  $("#agtest").onclick = async (ev) => {
    ev.target.disabled = true; ev.target.textContent = "Agenten tittar …";
    try {
      const r = await api("/api/agent/test", { method: "POST" });
      if (!r.ai_ready) toast("AI-hjälpen är inte inställd – agenten kan bara göra det enkla.", "bad");
      else openModal(`<h3>🤖 Agenten säger</h3><p translate="no" style="white-space:pre-wrap">${esc(r.text || "Inget svar.")}</p><div class="actions"><button class="btn primary" data-close>OK</button></div>`);
      agentCard();
    } catch (e) { toast(e.message, "bad"); }
    finally { ev.target.disabled = false; ev.target.textContent = "🧪 Prova agenten"; }
  };
}
