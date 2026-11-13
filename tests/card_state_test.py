"""Affichage de l'état (ouvert / fermé…) dans la carte, dans Chromium, sans Home Assistant.

La carte est chargée seule avec un objet `hass` simulé : on vérifie l'icône, le libellé, la couleur
par famille d'état, « depuis … », la mise à jour quand l'état change et l'absence d'erreur.
Usage : python tests/card_state_test.py
"""
import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

CARD = Path(__file__).resolve().parent.parent / "custom_components/code_pin/frontend/code-pin-card.js"
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("✅ " if cond else "❌ ") + name + (f" — {detail}" if detail and not cond else ""), flush=True)


PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body style="font-family:sans-serif">
<script>
const now = Date.now();
const iso = (minAgo) => new Date(now - minAgo * 60000).toISOString();
const LIB = { open: "Ouvert", closed: "Fermé", locked: "Verrouillé", unlocked: "Déverrouillé",
  disarmed: "Désarmé", armed_away: "Armé (absence)", triggered: "Déclenchée", on: "Activé", off: "Désactivé",
  unavailable: "Indisponible" };
const S = (id, state, minAgo, name) => ({ entity_id: id, state, last_changed: iso(minAgo), last_updated: iso(minAgo),
  attributes: { friendly_name: name } });
window.hass = {
  user: { id: "u1" },
  states: {
    "cover.porte_ecurie": S("cover.porte_ecurie", "closed", 12, "Porte écurie"),
    "cover.grande_porte": S("cover.grande_porte", "open", 150, "Grande porte"),
    "lock.porte_grange": S("lock.porte_grange", "locked", 0.2, "Serrure"),
    "alarm_control_panel.alarmo": S("alarm_control_panel.alarmo", "armed_away", 3000, "Alarme"),
    "input_boolean.vigilance": S("input_boolean.vigilance", "on", 5, "Vigilance"),
    "lock.coffre": S("lock.coffre", "unavailable", 30, "Coffre"),
    "cover.grange_porte_ecurie": S("cover.grange_porte_ecurie", "open", 2, "Porte écurie (Grange)"),
  },
  formatEntityState: (st) => LIB[st.state] || st.state,
  callWS: async (m) => {
    if (m.type !== "code_pin/info") throw { code: "unknown_command" };
    const ent = {};
    for (const e of m.entites) ent[e] = { protege: e !== "input_boolean.vigilance", distant: e.startsWith("cover.grange_"),
      actions: [{ cle: "ouvrir", libelle: "Ouvrir", code: true }, { cle: "fermer", libelle: "Fermer", code: false }] };
    return { mode: "observation", moi: { type: "personne", cle: "richard", nom: "Richard", admin: true, a_un_code: true },
      entites: ent, personnes: [], synchro: {}, protegees: [] };
  },
};
</script>
<script type="module" src="/card.js"></script>
<div id="box" style="max-width:WIDTHpx"></div>
<script type="module">
await customElements.whenDefined("code-pin-card");
const c = document.createElement("code-pin-card");
c.setConfig({ title: "Accès", entities: Object.keys(window.hass.states), mon_code: false });
document.getElementById("box").appendChild(c);
c.hass = window.hass;
window.card = c;
</script></body></html>"""


async def run(width):
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = await b.new_page(viewport={"width": width, "height": 900}, locale="fr-FR")
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        html = PAGE.replace("WIDTH", str(width))
        await page.route("**/card.js", lambda r: r.fulfill(path=str(CARD), content_type="application/javascript"))
        await page.route("**/index.html", lambda r: r.fulfill(body=html, content_type="text/html; charset=utf-8"))
        await page.goto("http://test.local/index.html")
        await page.wait_for_function("() => window.card && window.card.shadowRoot.querySelectorAll('.ligne').length >= 7",
                                     timeout=10000)
        rows = await page.evaluate("""() => [...window.card.shadowRoot.querySelectorAll('.ligne')].map(l => ({
            nom: l.querySelector('b')?.textContent, etat: l.querySelector('.etat')?.textContent,
            cls: l.querySelector('.etat')?.className, depuis: l.querySelector('.depuis')?.textContent,
            ico: !!l.querySelector('.ico ha-state-icon'), couleur: l.querySelector('.etat') ?
              getComputedStyle(l.querySelector('.etat')).color : null, texte: l.textContent }))""")
        by = {r["nom"]: r for r in rows}
        tag = f"[{width}px]"
        check(f"{tag} une icône par ligne", all(r["ico"] for r in rows), rows)
        check(f"{tag} porte fermée : « Fermé » en vert", by["Porte écurie"]["etat"] == "Fermé"
              and "c-ferme" in by["Porte écurie"]["cls"], by["Porte écurie"])
        check(f"{tag} grande porte : « Ouvert » en orange", by["Grande porte"]["etat"] == "Ouvert"
              and "c-ouvert" in by["Grande porte"]["cls"], by["Grande porte"])
        check(f"{tag} ouvert et fermé de couleurs différentes",
              by["Porte écurie"]["couleur"] != by["Grande porte"]["couleur"], (by["Porte écurie"]["couleur"], by["Grande porte"]["couleur"]))
        check(f"{tag} serrure verrouillée = fermé", "c-ferme" in by["Serrure"]["cls"] and by["Serrure"]["depuis"] == "à l'instant",
              by["Serrure"])
        check(f"{tag} alarme armée", "c-arme" in by["Alarme"]["cls"] and by["Alarme"]["depuis"].startswith("depuis le"), by["Alarme"])
        check(f"{tag} « depuis 12 min » et « depuis 2 h 30 »", by["Porte écurie"]["depuis"] == "depuis 12 min"
              and by["Grande porte"]["depuis"] == "depuis 2 h 30", (by["Porte écurie"]["depuis"], by["Grande porte"]["depuis"]))
        check(f"{tag} indisponible signalé en rouge", "c-indispo" in by["Coffre"]["cls"] and by["Coffre"]["etat"] == "Indisponible")
        check(f"{tag} entité non protégée : état visible quand même", by["Vigilance"]["etat"] == "Activé"
              and "non protégé" in by["Vigilance"]["texte"], by["Vigilance"])
        check(f"{tag} entité de l'autre maison : état + « via l'autre maison »",
              by["Porte écurie (Grange)"]["etat"] == "Ouvert" and "via l'autre maison" in by["Porte écurie (Grange)"]["texte"])
        # changement d'état : la carte se met à jour
        await page.evaluate("""() => { const h = { ...window.hass, states: { ...window.hass.states } };
            h.states['cover.porte_ecurie'] = { ...h.states['cover.porte_ecurie'], state: 'open',
              last_changed: new Date().toISOString(), last_updated: new Date().toISOString() };
            window.card.hass = h; }""")
        after = await page.evaluate("""() => { const l = [...window.card.shadowRoot.querySelectorAll('.ligne')]
            .find(x => x.querySelector('b').textContent === 'Porte écurie');
            return { etat: l.querySelector('.etat').textContent, cls: l.querySelector('.etat').className,
              depuis: l.querySelector('.depuis').textContent }; }""")
        check(f"{tag} ouverture affichée aussitôt", after["etat"] == "Ouvert" and "c-ouvert" in after["cls"]
              and after["depuis"] == "à l'instant", after)
        buttons = await page.evaluate("() => window.card.shadowRoot.querySelectorAll('button[data-act]').length")
        check(f"{tag} boutons d'action toujours là", buttons == 12, buttons)
        overflow = await page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        check(f"{tag} pas de débordement horizontal", overflow)
        os.makedirs("/tmp/cp_shots", exist_ok=True)
        await page.screenshot(path=f"/tmp/cp_shots/carte_{width}.png", full_page=True)
        check(f"{tag} aucune erreur JavaScript", not errors, errors[:3])
        await b.close()


async def main():
    for w in (1100, 380):
        await run(w)
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} vérifications OK")
    sys.exit(0 if all(RESULTS) else 1)


asyncio.run(main())
