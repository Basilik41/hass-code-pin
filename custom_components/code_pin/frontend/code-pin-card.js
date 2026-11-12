/* Codes d'accès — carte « custom:code-pin-card ».
 * Actions protégées par un code personnel ; sur une tablette murale on choisit sa personne puis on tape son code.
 * Options : entities (liste), title, mon_code (true), gestion (false, administrateurs), journal (false, administrateurs).
 */
const VERSION = "1.0.1";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const IDLE_MS = 30000;

const CSS = `
:host { display:block; }
ha-card { padding: 12px 16px 16px; }
.titre { font-size: 1.15em; font-weight: 500; margin: 4px 0 10px; display:flex; align-items:center; gap:8px; }
.titre .mode { margin-left:auto; font-size:.72em; padding:2px 8px; border-radius:10px; background: var(--secondary-background-color); color: var(--secondary-text-color); }
.ligne { display:flex; align-items:center; gap:10px; padding:8px 0; border-top:1px solid var(--divider-color); flex-wrap:wrap; }
.ligne:first-of-type { border-top:none; }
.nom { flex:1 1 140px; min-width:0; }
.nom b { font-weight:500; display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.nom small { color: var(--secondary-text-color); }
.btns { display:flex; gap:6px; flex-wrap:wrap; }
button.b { font: inherit; font-size:.9em; border:1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color);
  border-radius: 18px; padding: 6px 12px; cursor:pointer; min-height:36px; }
button.b.p { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
button.b.d { color: var(--error-color); }
button.b:disabled { opacity:.5; cursor:default; }
button.b:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.sec { margin-top: 14px; }
.sec h3 { font-size: .95em; font-weight: 500; margin: 0 0 6px; color: var(--secondary-text-color); }
.note { color: var(--secondary-text-color); font-size: .88em; margin: 6px 0; }
.warn { color: var(--warning-color, #e69500); }
.ok { color: var(--success-color, #2e7d32); }
.err { color: var(--error-color); }
.toast { margin-top:8px; padding:8px 10px; border-radius:8px; background: var(--secondary-background-color); font-size:.9em; }
table { width:100%; border-collapse: collapse; font-size:.85em; }
td, th { text-align:left; padding:4px 6px; border-top:1px solid var(--divider-color); vertical-align: top; }
th { color: var(--secondary-text-color); font-weight: 500; border-top: none; }
@media (max-width: 500px) { .opt { display:none; } }
`;

const MODAL_CSS = `
.fond { position:fixed; inset:0; background: rgba(0,0,0,.55); z-index: 9999; display:flex; align-items:center; justify-content:center; }
.boite { background: var(--card-background-color, #fff); color: var(--primary-text-color, #000); border-radius: 16px; width: min(360px, 94vw);
  max-height: 94vh; overflow:auto; padding: 18px 18px 14px; box-shadow: 0 8px 30px rgba(0,0,0,.35); font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
.boite.secoue { animation: secoue .35s; }
@keyframes secoue { 20%,60% { transform: translateX(-8px) } 40%,80% { transform: translateX(8px) } }
h2 { margin: 0 0 4px; font-size: 1.15em; font-weight: 500; }
.sous { color: var(--secondary-text-color, #666); font-size:.9em; margin-bottom: 12px; }
.pers { display:grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 10px; }
.pers button { border: 1px solid var(--divider-color, #ddd); background: none; border-radius: 12px; padding: 10px 4px; cursor:pointer; color: inherit; font: inherit; }
.pers button:focus-visible, .clav button:focus-visible { outline: 2px solid var(--primary-color, #03a9f4); }
.av { width:56px; height:56px; border-radius:50%; margin: 0 auto 6px; display:flex; align-items:center; justify-content:center;
  background: var(--primary-color, #03a9f4); color:#fff; font-size: 1.4em; overflow:hidden; }
.av img { width:100%; height:100%; object-fit:cover; }
.points { display:flex; justify-content:center; gap: 12px; margin: 6px 0 14px; min-height: 18px; }
.points span { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--primary-color, #03a9f4); }
.points span.r { background: var(--primary-color, #03a9f4); }
.clav { display:grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.clav button { font: inherit; font-size: 1.5em; min-height: 60px; border-radius: 14px; border: 1px solid var(--divider-color, #ddd);
  background: var(--secondary-background-color, #f3f3f3); color: inherit; cursor:pointer; touch-action: manipulation; }
.clav button.v { background: var(--primary-color, #03a9f4); color: #fff; border-color: transparent; }
.clav button:disabled { opacity: .4; cursor: default; }
.msg { min-height: 1.3em; text-align:center; margin: 0 0 10px; font-size: .92em; }
.msg.e { color: var(--error-color, #db4437); }
.msg.o { color: var(--success-color, #2e7d32); }
.pied { display:flex; justify-content: space-between; margin-top: 12px; }
.pied button { font: inherit; background:none; border:none; color: var(--primary-color, #03a9f4); cursor:pointer; padding: 8px; }
.fait { text-align:center; font-size: 3em; color: var(--success-color, #2e7d32); margin: 20px 0; }
`;

const ERREURS = {
  code_faux: (r) => `Code incorrect${r.essais != null ? ` — encore ${r.essais} essai${r.essais > 1 ? "s" : ""}` : ""}.`,
  bloque: (r) => `Code bloqué encore ${Math.ceil((r.secondes || 60) / 60)} min.`,
  appareil_bloque: (r) => `Appareil bloqué encore ${Math.ceil((r.secondes || 60) / 60)} min.`,
};

class CodePinModal {
  constructor(card) {
    this.card = card;
    this.host = document.createElement("div");
    this.root = this.host.attachShadow({ mode: "open" });
    this.flow = null;
    this._key = (e) => this._onKey(e);
  }

  open(flow) {
    this.flow = { buffer: "", msg: "", msgKind: "", ...flow };
    if (!this.host.isConnected) document.body.appendChild(this.host);
    document.addEventListener("keydown", this._key, true);
    this._render();
    this._touch();
  }

  close() {
    clearTimeout(this._idle);
    if (this.flow) this.flow.buffer = "";
    this.flow = null;
    document.removeEventListener("keydown", this._key, true);
    this.host.remove();
    this.card._focusBack();
  }

  _touch() {
    clearTimeout(this._idle);
    this._idle = setTimeout(() => this.close(), IDLE_MS);
  }

  _onKey(e) {
    if (!this.flow) return;
    const f = this.flow;
    if (e.key === "Escape") { e.stopPropagation(); this.close(); return; }
    if (f.step === "fait" || f.step === "personne") return;
    if (/^[0-9]$/.test(e.key)) { e.preventDefault(); e.stopPropagation(); this._digit(e.key); }
    else if (e.key === "Backspace") { e.preventDefault(); e.stopPropagation(); this._back(); }
    else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); this._submit(); }
  }

  _digit(d) {
    const f = this.flow;
    if (f.busy || f.buffer.length >= this.card.info.pin_max) return;
    f.buffer += d; f.msg = ""; this._touch(); this._render();
  }

  _back() { const f = this.flow; if (f.busy) return; f.buffer = f.buffer.slice(0, -1); this._touch(); this._render(); }

  async _submit() {
    const f = this.flow;
    if (f.busy || f.buffer.length < this.card.info.pin_min) return;
    const val = f.buffer;
    f.buffer = "";
    this._touch();
    await this.card._step(f, val);
  }

  error(msg, shake = true) {
    if (!this.flow) return;
    this.flow.msg = msg; this.flow.msgKind = "e"; this.flow.busy = false;
    this._render();
    if (shake) {
      const b = this.root.querySelector(".boite");
      b?.classList.remove("secoue"); void b?.offsetWidth; b?.classList.add("secoue");
    }
  }

  done(text) {
    if (!this.flow) return;
    this.flow.step = "fait"; this.flow.msg = text; this.flow.msgKind = "o"; this.flow.busy = false;
    this._render();
    clearTimeout(this._idle);
    this._idle = setTimeout(() => this.close(), 1400);
  }

  _render() {
    const f = this.flow;
    if (!f) return;
    const info = this.card.info;
    let body = "";
    if (f.step === "personne") {
      const ppl = info.personnes.filter((p) => p.a_un_code);
      body = ppl.length ? `<div class="pers">${ppl.map((p) => `
          <button data-p="${esc(p.cle)}" aria-label="${esc(p.nom)}">
            <div class="av">${p.photo ? `<img src="${esc(p.photo)}" alt="">` : esc((p.nom || "?").slice(0, 1).toUpperCase())}</div>
            ${esc(p.nom)}${p.bloque ? `<div class="msg e">bloqué</div>` : ""}
          </button>`).join("")}</div>`
        : `<p class="msg e">Personne n'a encore de code. Chacun le définit depuis son propre téléphone (« Mon code »).</p>`;
    } else if (f.step === "fait") {
      body = `<div class="fait" aria-hidden="true">✓</div>`;
    } else {
      const n = f.buffer.length;
      const dots = Array.from({ length: Math.max(info.pin_min, n) }, (_, i) => `<span class="${i < n ? "r" : ""}"></span>`).join("");
      body = `<div class="points" aria-label="${n} chiffre${n > 1 ? "s" : ""} saisi${n > 1 ? "s" : ""}">${dots}</div>
        <div class="clav">${["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => `<button data-d="${d}">${d}</button>`).join("")}
          <button data-k="back" aria-label="Effacer">⌫</button><button data-d="0">0</button>
          <button data-k="ok" class="v" aria-label="Valider" ${n < info.pin_min || f.busy ? "disabled" : ""}>✓</button></div>`;
    }
    this.root.innerHTML = `<style>${MODAL_CSS}</style>
      <div class="fond" part="fond"><div class="boite" role="dialog" aria-modal="true" aria-labelledby="t">
        <h2 id="t">${esc(f.title)}</h2>
        <div class="sous">${esc(this._subtitle())}</div>
        <p class="msg ${f.msgKind}" role="status" aria-live="assertive">${esc(f.msg || "")}</p>
        ${body}
        <div class="pied">${f.step !== "fait" ? `<button data-k="annuler">Annuler</button>` : "<span></span>"}
          ${f.step === "code" && f.canChangePerson ? `<button data-k="changer">Changer de personne</button>` : ""}</div>
      </div></div>`;
    const R = this.root;
    R.querySelector(".fond").addEventListener("click", (e) => { if (e.target.classList.contains("fond")) this.close(); });
    R.querySelectorAll("[data-d]").forEach((b) => b.addEventListener("click", () => this._digit(b.dataset.d)));
    R.querySelector('[data-k="back"]')?.addEventListener("click", () => this._back());
    R.querySelector('[data-k="ok"]')?.addEventListener("click", () => this._submit());
    R.querySelector('[data-k="annuler"]')?.addEventListener("click", () => this.close());
    R.querySelector('[data-k="changer"]')?.addEventListener("click", () => { f.step = "personne"; f.personne = null; f.msg = ""; this._touch(); this._render(); });
    R.querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => {
      f.personne = b.dataset.p; f.step = "code"; f.msg = ""; f.canChangePerson = true; this._touch(); this._render();
    }));
    (R.querySelector("[data-p]") || R.querySelector('[data-d="1"]') || R.querySelector('[data-k="annuler"]'))?.focus();
  }

  _subtitle() {
    const f = this.flow;
    const info = this.card.info;
    if (f.step === "personne") return "Qui êtes-vous ?";
    if (f.step === "fait") return f.msg || "";
    const p = info.personnes.find((x) => x.cle === f.personne);
    const who = p ? `${p.nom} — ` : "";
    return who + ({ code: "votre code", ancien: "code actuel", nouveau: `nouveau code (${info.pin_min} à ${info.pin_max} chiffres)`,
      confirmer: "retapez le nouveau code", code_admin: "votre code d'administrateur" }[f.step] || "");
  }
}

class CodePinCard extends HTMLElement {
  setConfig(config) {
    if (config.entities && !Array.isArray(config.entities)) throw new Error("entities : liste d'entités");
    this.config = { mon_code: true, gestion: false, journal: false, ...config };
    this.ents = (config.entities || []).map((e) => (typeof e === "string" ? { entity: e } : e)).filter((e) => e && e.entity);
    this.info = null;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  getCardSize() { return 2 + this.ents.length; }

  static getStubConfig() { return { title: "Accès protégés", entities: [] }; }

  set hass(h) {
    const first = !this._hass;
    this._hass = h;
    if (first || (h.user && this._uid !== h.user.id)) { this._uid = h.user?.id; this._load(); return; }
    const sig = this.ents.map((e) => { const s = h.states[e.entity]; return s ? s.state + s.last_updated : "-"; }).join("|");
    if (sig !== this._sig) { this._sig = sig; this._render(); }
  }

  connectedCallback() {
    this._timer = setInterval(() => this._load(), 60000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this.modal?.close();
  }

  async _ws(msg) { return this._hass.callWS(msg); }

  async _load() {
    if (!this._hass) return;
    try {
      this.info = await this._ws({ type: "code_pin/info", entites: this.ents.map((e) => e.entity) });
      this._err = null;
      if (this.config.journal && this.info.moi.admin) {
        const j = await this._ws({ type: "code_pin/journal", jours: 30, limite: 40 });
        this._journal = j.entrees || [];
      }
    } catch (e) {
      this._err = e?.code === "unknown_command" ? "Module « Codes d'accès » non installé." : (e?.message || String(e));
    }
    this._render();
  }

  _toast(text, kind = "") {
    this._msg = { text, kind };
    this._render();
    clearTimeout(this._toastT);
    this._toastT = setTimeout(() => { this._msg = null; this._render(); }, 6000);
  }

  _focusBack() { this._lastBtn?.focus?.(); }

  _render() {
    const R = this.shadowRoot;
    if (!R || !this.config) return;
    const info = this.info;
    const h = this._hass;
    let html = `<style>${CSS}</style><ha-card>`;
    html += `<div class="titre"><span>🔐 ${esc(this.config.title || "Accès protégés")}</span>${info && info.moi.admin ? `<span class="mode">${info.mode === "blocage" ? "blocage" : "observation"}</span>` : ""}</div>`;
    if (this._err) html += `<p class="note err">${esc(this._err)}</p>`;
    else if (!info || !h) html += `<p class="note">Chargement…</p>`;
    else {
      html += this._actionsHtml(info, h) + this._monCodeHtml(info) + this._gestionHtml(info) + this._journalHtml(info);
    }
    if (this._msg) html += `<div class="toast ${this._msg.kind}" role="status">${esc(this._msg.text)}</div>`;
    html += `</ha-card>`;
    R.innerHTML = html;
    R.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
      this._lastBtn = b; this._startAction(b.dataset.ent, b.dataset.act);
    }));
    R.querySelectorAll("[data-g]").forEach((b) => b.addEventListener("click", () => { this._lastBtn = b; this._startGestion(b.dataset.g, b.dataset.p); }));
  }

  _actionsHtml(info, h) {
    if (!this.ents.length) return "";
    const rows = this.ents.map((c) => {
      const st = h.states[c.entity];
      const d = info.entites[c.entity];
      const name = c.name || st?.attributes?.friendly_name || c.entity;
      if (!st) return `<div class="ligne"><div class="nom"><b>${esc(name)}</b><small class="err">introuvable</small></div></div>`;
      const etat = h.formatEntityState ? h.formatEntityState(st) : st.state;
      if (!d?.protege) {
        return `<div class="ligne"><div class="nom"><b>${esc(name)}</b><small class="warn">${info.moi.admin ? "non protégé : ajouter l'étiquette « Protégé par code »" : "non protégé"}</small></div></div>`;
      }
      const btns = d.actions.map((a) => `<button class="b ${a.code ? "p" : ""}" data-ent="${esc(c.entity)}" data-act="${esc(a.cle)}"
          aria-label="${esc(a.libelle)} ${esc(name)}${a.code ? " (code demandé)" : ""}">${a.code ? "🔒 " : ""}${esc(a.libelle)}</button>`).join("");
      return `<div class="ligne"><div class="nom"><b>${esc(name)}</b><small>${esc(etat)}${d.distant ? " · via l'autre maison" : ""}</small></div><div class="btns">${btns}</div></div>`;
    }).join("");
    return `<div>${rows}</div>`;
  }

  _monCodeHtml(info) {
    if (!this.config.mon_code) return "";
    const m = info.moi;
    let inner;
    if (m.type === "personne") {
      inner = `<div class="ligne"><div class="nom"><b>${esc(m.nom)}</b><small class="${m.a_un_code ? "ok" : "warn"}">${m.a_un_code ? "code défini" : "pas encore de code"}</small></div>
        <div class="btns"><button class="b p" data-g="mon_code">${m.a_un_code ? "Changer mon code" : "Définir mon code"}</button></div></div>`;
    } else if (m.type === "tablette") {
      inner = `<p class="note">Tablette « ${esc(m.appareil)} » : chacun choisit sa photo puis tape son code. Pour créer ou changer son code, utiliser son propre téléphone.</p>`;
    } else {
      inner = `<p class="note">Ce compte n'est relié à aucune personne : les actions protégées sont impossibles ici.</p>`;
    }
    return `<div class="sec"><h3>Mon code</h3>${inner}</div>`;
  }

  _gestionHtml(info) {
    if (!this.config.gestion || !info.moi.admin) return "";
    const rows = info.personnes.map((p) => `<div class="ligne"><div class="nom"><b>${esc(p.nom)}</b>
        <small class="${p.a_un_code ? "ok" : "warn"}">${p.a_un_code ? "code défini" : "pas de code"}${p.bloque ? ` · <span class="err">bloqué ${Math.ceil(p.bloque / 60)} min</span>` : ""}</small></div>
        <div class="btns">${!p.moi ? `<button class="b" data-g="definir" data-p="${esc(p.cle)}">Définir</button>` : ""}
        ${p.a_un_code ? `<button class="b d" data-g="effacer" data-p="${esc(p.cle)}">Effacer</button>` : ""}
        ${p.bloque ? `<button class="b" data-g="debloquer" data-p="${esc(p.cle)}">Débloquer</button>` : ""}</div></div>`).join("");
    const sync = Object.entries(info.synchro || {}).map(([k, v]) => `<div class="note ${v.ok ? "ok" : "err"}">${esc(k)} : ${v.ok ? "codes synchronisés" : esc(v.erreur || "erreur")}
        (${new Date(v.t * 1000).toLocaleTimeString()})</div>`).join("") || `<p class="note">Aucun autre Home Assistant relié.</p>`;
    return `<div class="sec"><h3>Codes de la famille</h3>${rows}
      <h3 style="margin-top:10px">Autre Home Assistant</h3>${sync}<button class="b" data-g="synchro">Synchroniser maintenant</button>
      <p class="note">Entités protégées ici : ${(info.protegees || []).length}. Pour en ajouter : appareil → ⚙️ → Étiquettes → « Protégé par code ».</p></div>`;
  }

  _journalHtml(info) {
    if (!this.config.journal || !info.moi.admin) return "";
    const RES = { ok: "✅", refuse: "❌ code faux", bloque: "🔒 bloqué", erreur: "⚠️ erreur", commande_directe: "⚠️ sans code" };
    const rows = (this._journal || []).map((e) => `<tr><td>${esc(new Date(e.t * 1000).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }))}</td>
        <td>${esc(e.personne)}${e.appareil ? `<br><small>tablette ${esc(e.appareil)}</small>` : ""}${e.site ? `<br><small>${esc(e.site)}</small>` : ""}</td>
        <td>${esc(e.nom_entite || e.entite || "")} <small>${esc(e.action)}</small></td>
        <td>${esc(RES[e.resultat] || e.resultat)}<span class="opt">${e.detail ? `<br><small>${esc(e.detail)}</small>` : ""}</span></td></tr>`).join("");
    return `<div class="sec"><h3>Journal (30 jours)</h3>${rows ? `<table><thead><tr><th>Quand</th><th>Qui</th><th>Quoi</th><th>Résultat</th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="note">Rien pour l'instant.</p>`}</div>`;
  }

  // ───────── déroulés ─────────
  _modal() { if (!this.modal) this.modal = new CodePinModal(this); return this.modal; }

  _startAction(entity, act) {
    const d = this.info.entites[entity];
    const a = d?.actions.find((x) => x.cle === act);
    if (!a) return;
    const st = this._hass.states[entity];
    const name = this.ents.find((e) => e.entity === entity)?.name || st?.attributes?.friendly_name || entity;
    if (!a.code) { this._exec({ kind: "action", entity, act, title: `${a.libelle} — ${name}` }, null); return; }
    const m = this.info.moi;
    if (m.type === "aucun") { this._toast("Ce compte n'est relié à aucune personne : action impossible ici.", "err"); return; }
    if (m.type === "personne" && !m.a_un_code) { this._toast("Définissez d'abord votre code (« Mon code »).", "warn"); return; }
    const tablet = m.type === "tablette";
    this._modal().open({ kind: "action", entity, act, title: `${a.libelle} — ${name}`, step: tablet ? "personne" : "code",
      personne: tablet ? null : m.cle });
  }

  _startGestion(what, cle) {
    const m = this.info.moi;
    if (what === "synchro") { this._sync(); return; }
    if (what === "mon_code") {
      this._modal().open({ kind: "mon_code", title: m.a_un_code ? "Changer mon code" : "Définir mon code",
        step: m.a_un_code ? "ancien" : "nouveau", personne: m.cle });
      return;
    }
    if (!m.a_un_code) { this._toast("Définissez d'abord votre propre code.", "warn"); return; }
    const p = this.info.personnes.find((x) => x.cle === cle);
    const titles = { definir: `Code de ${p?.nom}`, effacer: `Effacer le code de ${p?.nom}`, debloquer: `Débloquer ${p?.nom}` };
    this._modal().open({ kind: what, cible: cle, title: titles[what], step: "code_admin", personne: m.cle });
  }

  async _sync() {
    try { await this._ws({ type: "code_pin/synchroniser" }); this._toast("Synchronisation faite."); }
    catch (e) { this._toast(e.message || "Erreur", "err"); }
    this._load();
  }

  async _step(f, val) {
    const M = this.modal;
    if (f.kind === "action") { await this._exec(f, val); return; }
    if (f.step === "ancien") { f.ancien = val; f.step = "nouveau"; M._render(); return; }
    if (f.step === "code_admin") {
      f.code_admin = val;
      if (f.kind === "definir") { f.step = "nouveau"; f.personne = f.cible; M._render(); return; }
      f.busy = true; M._render();
      const r = await this._call({ type: `code_pin/${f.kind}`, personne: f.cible, code_admin: val });
      if (r.ok) { M.done(f.kind === "effacer" ? "Code effacé" : "Débloqué"); this._load(); }
      else M.error(this._errText(r));
      return;
    }
    if (f.step === "nouveau") { f.nouveau = val; f.step = "confirmer"; M._render(); return; }
    if (f.step === "confirmer") {
      if (val !== f.nouveau) { f.nouveau = ""; f.step = "nouveau"; M.error("Les deux codes sont différents : recommencez."); return; }
      f.busy = true; M._render();
      const msg = { type: "code_pin/definir", nouveau: f.nouveau };
      if (f.kind === "mon_code" && f.ancien) msg.ancien = f.ancien;
      if (f.kind === "definir") { msg.personne = f.cible; msg.code_admin = f.code_admin; }
      const r = await this._call(msg);
      f.nouveau = ""; f.ancien = ""; f.code_admin = "";
      if (r.ok) { M.done("Code enregistré"); this._load(); return; }
      // code faux (ancien / administrateur) : on reprend depuis le début
      f.step = f.kind === "mon_code" ? (this.info.moi.a_un_code ? "ancien" : "nouveau") : "code_admin";
      if (f.kind === "definir") f.personne = this.info.moi.cle;
      M.error(this._errText(r));
    }
  }

  async _exec(f, code) {
    const M = this.modal;
    if (M && M.flow) { f.busy = true; M._render(); }
    const msg = { type: "code_pin/executer", entity_id: f.entity, action: f.act };
    if (code) msg.code = code;
    if (code && f.personne) msg.personne = f.personne;
    const r = await this._call(msg);
    if (r.ok) {
      if (M && M.flow) M.done(r.qui ? `Fait — ${r.qui}` : "Fait");
      else this._toast(`${f.title} : fait.`, "ok");
      return;
    }
    if (M && M.flow) {
      M.error(this._errText(r));
      if (r.erreur === "bloque" || r.erreur === "appareil_bloque") this._load();
    } else this._toast(this._errText(r), "err");
  }

  async _call(msg) {
    try { return await this._ws(msg); }
    catch (e) { return { ok: false, erreur: "ws", message: e?.message || "Erreur de connexion" }; }
  }

  _errText(r) { return (ERREURS[r.erreur] ? ERREURS[r.erreur](r) : r.message) || "Erreur"; }
}

if (!customElements.get("code-pin-card")) {
  customElements.define("code-pin-card", CodePinCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "code-pin-card", name: "Codes d'accès", description: "Actions protégées par un code personnel" });
  console.info(`%c code-pin-card ${VERSION} `, "background:#c62828;color:#fff;border-radius:3px");
}
