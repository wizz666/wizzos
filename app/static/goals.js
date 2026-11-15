/* WizzOS – paket efter mål: "Vad vill du göra?" överst i Appbutiken. */
"use strict";

const FIT = { ok: "", warn: '<span class="badge warnb">Prova först</span>', bad: '<span class="badge no">Passar inte här</span>' };

async function drawGoals(view) {
  let d;
  try { d = await api("/api/goals"); } catch { return; }
  const head = $(".head", view); if (!head || $("#goals", view)) return;
  head.insertAdjacentHTML("afterend", `<section id="goals" class="goals fade-in">
    <h2 style="margin-top:0">Vad vill du göra? <span class="hint" style="font-weight:500">Välj ett mål – WizzOS väljer appen och ställer in allt.</span></h2>
    <div class="goal-grid">${d.goals.map((g) => `<button class="goal card ${g.installed ? "done" : ""}" type="button" data-goal="${esc(g.id)}">
      <span class="goal-emo">${g.emoji}</span>
      <b>${esc(g.title)}</b>
      <span class="goal-pitch">${esc(g.pitch.split(" – ")[0].split(". ")[0])}.</span>
      <span class="goal-foot">${g.installed ? `<span class="badge ok">✓ ${g.trial ? "På prov" : "Klart"}</span>` : FIT[g.fit] || ""}<i>${esc(g.app_title)}</i></span>
    </button>`).join("")}</div></section>`);
  $$("#goals [data-goal]").forEach((b) => b.onclick = () => goalModal(b.dataset.goal));
}

async function goalModal(gid) {
  let g;
  try { g = await api(`/api/goals/${encodeURIComponent(gid)}`); } catch (e) { toast(e.message, "bad"); return; }
  if (g.installed) return goalWelcome(g);
  const icon = { ok: "🟢", warn: "🟡", bad: "🔴" };
  const blocked = g.checks.some((c) => c.level === "bad" && /processorn|finns inte i dina/.test(c.text));
  openModal(`<div class="goal-modal">
    <div class="goal-big">${g.emoji}</div>
    <h3>${esc(g.title)}</h3>
    <p>${esc(g.pitch)}</p>
    <p class="hint">WizzOS installerar <b>${esc(g.app_title)}</b>, tar med den i den nattliga backupen och de säkra uppdateringarna, och visar sedan hur du kommer igång.</p>
    <div class="goal-checks">${g.checks.map((c) => `<div>${icon[c.level]} ${esc(c.text)}</div>`).join("")}</div>
    <div class="actions">
      <button class="btn" data-close>Avbryt</button>
      ${blocked ? "" : `<button class="btn" id="gtry">🧪 Prova i 1 timme</button><button class="btn primary" id="gins">Installera</button>`}
    </div></div>`);
  const go = (trial) => async () => {
    try {
      const r = await api(`/api/goals/${encodeURIComponent(gid)}/install`, { method: "POST", body: { trial_minutes: trial } });
      closeModal();
      watchJob(r.job, { title: `${g.title}: installerar ${g.app_title}`, onDone: async (j) => {
        if (j.state !== "done") return;
        storeState.data = null;
        const fresh = await api(`/api/goals/${encodeURIComponent(gid)}`);
        goalWelcome(fresh, true);
        if (location.hash.startsWith("#/store")) { $("#goals")?.remove(); drawGoals($("#view")); }
      } });
    } catch (e) { toast(e.message, "bad"); }
  };
  $("#gtry")?.addEventListener("click", go(60));
  $("#gins")?.addEventListener("click", go(0));
}

function goalWelcome(g, fresh = false) {
  const url = g.url || "";
  const mob = g.mobile || {};
  openModal(`<div class="goal-modal">
    <div class="goal-big">${fresh ? "🎉" : g.emoji}</div>
    <h3>${fresh ? `${esc(g.title)} är klart!` : esc(g.title)}</h3>
    ${g.trial ? `<p class="hint">🧪 ${esc(g.app_title)} är på prov. Gillar du den – tryck ♥ Behåll på startsidan, annars försvinner den spårlöst.</p>` : `<p class="hint">✅ Ingår i nattlig backup och säkra uppdateringar.</p>`}
    <h4>Så kommer du igång</h4>
    <ol class="goal-steps">${g.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
    ${url ? `<div class="phone" style="margin-top:12px"><div class="phone-qr"><img src="/api/qr?text=${encodeURIComponent(url)}" alt="QR-kod till ${esc(g.app_title)}" width="150" height="150"></div>
      <div><b>Adress</b><p class="mono-url">${esc(url)}</p>
      ${mob.ios || mob.android ? `<p class="hint">Mobilappen: ${mob.ios ? `<a href="${esc(mob.ios)}" target="_blank" rel="noopener">iPhone</a>` : ""}${mob.ios && mob.android ? " · " : ""}${mob.android ? `<a href="${esc(mob.android)}" target="_blank" rel="noopener">Android</a>` : ""}</p>` : ""}
      ${isPrivUrl(url) ? `<p class="hint">🔒 Adressen nås via Tailscale eller hemma.</p>` : ""}</div></div>` : ""}
    <div class="actions"><button class="btn" data-close>Stäng</button>${url ? `<a class="btn primary" href="${esc(url)}" target="_blank" rel="noopener">Öppna ${esc(g.app_title)} ↗</a>` : ""}</div>
  </div>`);
}

/* Lägg målen överst i Appbutiken */
if (typeof pages !== "undefined" && pages.store && !pages.store._goals) {
  const orig = pages.store;
  pages.store = async function (view) { await orig(view); drawGoals(view); };
  pages.store._goals = true;
}
