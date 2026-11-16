/* WizzOS – språk.
   Gränssnittet skrivs på svenska. Väljer man ett annat språk byter det här
   lagret ut texten i sidan (textnoder, placeholder, title, aria-label) med
   hjälp av en ordlista /static/i18n/<språk>.json där nyckeln är den svenska
   texten. Nycklar med {namn} blir mönster, t.ex. "{n} appar" → "{n} apps".
   Saknas en översättning visas svenskan (och den hamnar i I18N.misses). */
"use strict";

const LANGS = { sv: "Svenska", en: "English" };
const I18N = { lang: "sv", dict: new Map(), pats: [], misses: new Set(), done: new WeakMap(), attrs: new WeakMap() };

function langPref() {
  let l = null;
  try { l = localStorage.getItem("wz-lang"); } catch (e) { /* privat läge */ }
  if (!l) { const m = document.cookie.match(/(?:^|; )wz-lang=(\w+)/); l = m && m[1]; }
  if (!l) l = /^sv|^nb|^no|^da/i.test(navigator.language || "") ? "sv" : "en";
  return LANGS[l] ? l : "sv";
}

function setLang(l) {
  if (!LANGS[l]) return;
  try { localStorage.setItem("wz-lang", l); } catch (e) { /* privat läge */ }
  document.cookie = `wz-lang=${l}; path=/; max-age=31536000; samesite=strict`;
  // Ägarens val blir också serverns språk (larm, veckobrev, AI-kopplingen).
  const done = () => location.reload();
  if (window.ME && window.ME.id === "owner") {
    fetch("/api/settings", { method: "PUT", credentials: "same-origin",
      headers: { "x-wizzos": "1", "content-type": "application/json" }, body: JSON.stringify({ language: l }) })
      .finally(done);
  } else done();
}

const loc = () => (I18N.lang === "en" ? "en-GB" : "sv-SE");

/* t("Svensk text", {n: 3}) – för text som inte går via sidan (t.ex. document.title). */
function t(s, vars) {
  let out = I18N.lang === "sv" ? s : (I18N.dict.get(s) ?? trCore(s) ?? s);
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  return out;
}

const esc_re = (s) => s.replace(/[.*+?^$()|[\]\\]/g, "\\$&");

function buildPatterns(dict) {
  const pats = [];
  for (const [k, v] of dict) {
    if (!/\{\w+\}/.test(k)) continue;
    const names = [];
    const src = esc_re(k).replace(/\\?\{(\w+)\\?\}/g, (m, n) => { names.push(n); return "(.*?)"; });
    pats.push({ re: new RegExp(`^${src}$`, "s"), names, out: v, len: k.length });
  }
  return pats.sort((a, b) => b.len - a.len);
}

function trCore(key) {
  const hit = I18N.dict.get(key);
  if (hit !== undefined) return hit;
  for (const p of I18N.pats) {
    const m = key.match(p.re);
    if (!m) continue;
    const vals = {};
    p.names.forEach((n, i) => { const v = m[i + 1]; vals[n] = (SWEDISH.test(v) && v.length < key.length && trSmart(v)) || v; });
    return p.out.replace(/\{(\w+)\}/g, (x, n) => (n in vals ? vals[n] : x));
  }
  return null;
}

const SWEDISH = /[a-zåäö]{2}/i;
const PREFIX = /^([\p{Extended_Pictographic}\p{So}️‍•↶⬆⬇⧉+]+\s*)(.+)$/u;

/* Hela texten först; annars utan inledande symbol (🟢, 📦 …); annars del för del
   runt " · " (t.ex. "Mapp · för 2 dagar sedan"). */
function trSmart(key) {
  let out = trCore(key);
  if (out !== null) return out;
  const m = key.match(PREFIX);
  if (m) { out = trSmart(m[2]); if (out !== null) return m[1] + out; }
  if (key.includes(" · ")) {
    let any = false, bad = false;
    const parts = key.split(" · ").map((p) => {
      if (!SWEDISH.test(p)) return p;
      const r = trSmart(p);
      if (r === null) { bad = bad || /[åäö]|\b(och|att|för|inte|med|som|är|har|sedan)\b/i.test(p); return p; }
      any = true; return r;
    });
    if (any && !bad) return parts.join(" · ");
  }
  return null;
}
function tr(s) {
  const key = s.trim().replace(/\s+/g, " ");
  if (!key || !SWEDISH.test(key)) return null;
  const out = trSmart(key);
  if (out === null) { I18N.misses.add(key); return null; }
  const lead = s.match(/^\s*/)[0], tail = s.match(/\s*$/)[0];
  return lead + out + tail;
}

const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE", "PRE", "KBD"]);
const ATTRS = ["placeholder", "title", "aria-label", "alt"];

function skipEl(el) {
  for (let e = el; e && e !== document.body; e = e.parentElement) {
    if (SKIP.has(e.tagName) || e.getAttribute("translate") === "no" || e.isContentEditable) return true;
  }
  return false;
}

function trText(node) {
  if (I18N.done.get(node) === node.data || skipEl(node.parentElement)) return;
  const out = tr(node.data);
  if (out !== null && out !== node.data) node.data = out;
  I18N.done.set(node, node.data);
}

function trAttrs(el) {
  if (skipEl(el)) return;
  for (const a of ATTRS) {
    const v = el.getAttribute(a);
    let seen = I18N.attrs.get(el);
    if (!seen) { seen = {}; I18N.attrs.set(el, seen); }
    if (!v || seen[a] === v) continue;
    const out = tr(v);
    seen[a] = out ?? v;
    if (out !== null) el.setAttribute(a, out);
  }
}

function trTree(root) {
  if (root.nodeType === 3) { trText(root); return; }
  if (root.nodeType !== 1) return;
  trAttrs(root);
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeType === 3 ? trText(n) : trAttrs(n);
}

async function i18nInit() {
  I18N.lang = langPref();
  document.documentElement.lang = I18N.lang;
  if (I18N.lang === "sv") return;
  try {
    const r = await fetch(`/static/i18n/${I18N.lang}.json`, { cache: "no-cache" });
    I18N.dict = new Map(Object.entries(await r.json()));
  } catch (e) { I18N.lang = "sv"; return; }
  I18N.pats = buildPatterns(I18N.dict);
  trTree(document.head.querySelector("title"));
  trTree(document.body);
  new MutationObserver((list) => {
    for (const m of list) {
      if (m.type === "childList") m.addedNodes.forEach(trTree);
      else if (m.type === "characterData") trText(m.target);
      else trAttrs(m.target);
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
}

/* Liten språkväljare (används på inloggningen, i Inställningar och Mitt konto). */
function langPicker() {
  return `<span class="lang-pick" translate="no">${Object.entries(LANGS).map(([k, v]) =>
    `<button type="button" class="${k === I18N.lang ? "on" : ""}" onclick="setLang('${k}')">${v}</button>`).join("")}</span>`;
}

window.I18N_READY = i18nInit();
