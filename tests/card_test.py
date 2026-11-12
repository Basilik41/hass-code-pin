"""Carte des codes d'accès dans Chromium (banc A, après bench_code_pin.py)."""
import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp
from playwright.async_api import async_playwright

sys.path.insert(0, "/home/claude/hass-acces-distant/tests")
from card_app_test import storage_js, tokens_for

HA = "http://127.0.0.1:8123"
OUT = "/home/claude/bench/shots"
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")
RESULTS = []
PORTE, SERRURE, ALARME, VIGI = "cover.porte_banc", "lock.serrure_banc", "alarm_control_panel.alarme_banc", \
    "input_boolean.vigilance_banc"
FIND = """(tag) => { const walk = (root) => { const f = root.querySelector(tag); if (f) return f;
  for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) { const r = walk(el.shadowRoot); if (r) return r; } }
  return null; }; return walk(document); }"""
MODAL = "() => { for (const d of document.body.children) if (d.shadowRoot && d.shadowRoot.querySelector('.boite')) return d.shadowRoot; return null; }"


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("✅ " if cond else "❌ ") + name + (f" — {detail}" if detail and not cond else ""), flush=True)


async def ws(s, token, msgs):
    out = []
    async with s.ws_connect(f"{HA}/api/websocket") as w:
        await w.receive_json()
        await w.send_json({"type": "auth", "access_token": token})
        await w.receive_json()
        for i, m in enumerate(msgs, 1):
            await w.send_json({**m, "id": i})
            while True:
                r = await w.receive_json()
                if r.get("id") == i and r.get("type") == "result":
                    out.append(r)
                    break
    return out


async def st(s, token, eid):
    async with s.get(f"{HA}/api/states/{eid}", headers={"Authorization": f"Bearer {token}"}) as r:
        return (await r.json())["state"]


async def open_card(browser, tok, name, width=1280, height=1000):
    ctx = await browser.new_context(viewport={"width": width, "height": height}, locale="fr-FR")
    await ctx.add_init_script(storage_js(tok))
    page = await ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    await page.goto(f"{HA}/cp-test/0", wait_until="networkidle")
    await page.locator("code-pin-card").wait_for(timeout=30000)
    await page.wait_for_timeout(2000)
    await page.screenshot(path=f"{OUT}/cp-{name}.png", full_page=True)
    card = await page.evaluate_handle(FIND, "code-pin-card")
    return ctx, page, card, errors


async def modal_eval(page, js):
    return await page.evaluate(f"() => {{ const m = ({MODAL})(); return m ? ({js})(m) : null; }}")


async def keypad(page, digits):
    for d in digits:
        await modal_eval(page, f"m => m.querySelector('[data-d=\"{d}\"]').click()")
        await page.wait_for_timeout(60)
    await modal_eval(page, "m => m.querySelector('[data-k=\"ok\"]').click()")
    await page.wait_for_timeout(1200)


async def click_action(card, page, entity, act):
    await card.evaluate(f"c => c.shadowRoot.querySelector('[data-ent=\"{entity}\"][data-act=\"{act}\"]').click()")
    await page.wait_for_timeout(500)


async def main():
    os.makedirs(OUT, exist_ok=True)
    async with aiohttp.ClientSession() as s:
        tok = json.loads(Path("/home/claude/bench/tokens.json").read_text())
        admin = await tokens_for(s, refresh=tok["refresh"])
        julie = await tokens_for(s, username="julie", password="motdepasse-julie")
        tab = await tokens_for(s, username="tabsalon", password="motdepasse-tab")
        at = admin["access_token"]
        dash = await ws(s, at, [{"type": "lovelace/dashboards/list"}])
        if not any(d["url_path"] == "cp-test" for d in dash[0]["result"]):
            await ws(s, at, [{"type": "lovelace/dashboards/create", "url_path": "cp-test", "title": "CP test",
                              "mode": "storage", "require_admin": False, "show_in_sidebar": False}])
        await ws(s, at, [{"type": "lovelace/config/save", "url_path": "cp-test", "config": {
            "views": [{"title": "Accès", "path": "0", "type": "sections", "sections": [{"type": "grid", "cards": [
                {"type": "custom:code-pin-card", "title": "Accès Grange", "gestion": True, "journal": True,
                 "entities": [PORTE, SERRURE, ALARME, {"entity": VIGI, "name": "Surveillance"}, "light.inconnue"]}]}]}]}}])
        # état de départ
        await ws(s, at, [{"type": "code_pin/executer", "entity_id": PORTE, "action": "fermer"},
                         {"type": "code_pin/debloquer", "personne": "julie", "code_admin": "86420"},
                         {"type": "code_pin/debloquer", "personne": "richard", "code_admin": "86420"}])

        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=CHROME)
            # ── tablette murale ──
            ctx, page, card, errors = await open_card(browser, tab, "tablette", 1024, 768)
            txt = await card.evaluate("c => c.shadowRoot.querySelector('ha-card').innerText")
            check("tablette : actions listées avec cadenas", "🔒 Ouvrir" in txt and "Fermer" in txt, txt[:300])
            check("tablette : explication « choisir sa photo »", "choisit sa photo" in txt, txt)
            check("tablette : pas de gestion ni de journal", "Codes de la famille" not in txt and "Journal" not in txt)
            await click_action(card, page, PORTE, "ouvrir")
            ppl = await modal_eval(page, "m => [...m.querySelectorAll('[data-p]')].map(b => b.dataset.p)")
            check("tablette : choix de la personne", ppl and set(ppl) >= {"julie", "richard"}, ppl)
            await page.screenshot(path=f"{OUT}/cp-tablette-personnes.png")
            await modal_eval(page, "m => m.querySelector('[data-p=\"julie\"]').click()")
            await page.wait_for_timeout(300)
            sub = await modal_eval(page, "m => m.querySelector('.sous').innerText")
            check("tablette : « Julie — votre code »", sub and "Julie" in sub, sub)
            await keypad(page, "1111")
            msg = await modal_eval(page, "m => m.querySelector('.msg').innerText")
            check("code faux : message et essais restants", msg and "Code incorrect" in msg and "essai" in msg, msg)
            await page.screenshot(path=f"{OUT}/cp-tablette-erreur.png")
            # clavier physique
            await page.keyboard.type("5938")
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(700)
            done = await modal_eval(page, "m => m.querySelector('.fait') ? m.querySelector('.msg').innerText : null")
            check("bon code (clavier) : « Fait — Julie (tablette Salon) »", done and "Julie (tablette Salon)" in done, done)
            check("la porte est ouverte", await st(s, at, PORTE) == "open")
            await page.wait_for_timeout(2000)
            check("la fenêtre se ferme seule", await page.evaluate(f"() => ({MODAL})() === null"))
            await click_action(card, page, PORTE, "fermer")
            await page.wait_for_timeout(1000)
            check("fermer : sans code, directement", await st(s, at, PORTE) == "closed")
            # Échap / « Changer de personne »
            await click_action(card, page, SERRURE, "deverrouiller")
            await modal_eval(page, "m => m.querySelector('[data-p=\"richard\"]').click()")
            await page.wait_for_timeout(200)
            await modal_eval(page, "m => m.querySelector('[data-k=\"changer\"]').click()")
            await page.wait_for_timeout(200)
            back = await modal_eval(page, "m => !!m.querySelector('[data-p]')")
            check("« Changer de personne » revient au choix", back)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            check("Échap ferme la fenêtre", await page.evaluate(f"() => ({MODAL})() === null"))
            check("serrure toujours verrouillée", await st(s, at, SERRURE) == "locked")
            check("tablette : aucune erreur JavaScript", not errors, errors[:3])
            await ctx.close()

            # ── téléphone de Julie (petit écran) ──
            ctx, page, card, errors = await open_card(browser, julie, "julie-mobile", 390, 844)
            txt = await card.evaluate("c => c.shadowRoot.querySelector('ha-card').innerText")
            check("Julie : « code défini » et bouton « Changer mon code »", "code défini" in txt and "Changer mon code" in txt,
                  txt)
            await click_action(card, page, VIGI, "couper")
            ppl = await modal_eval(page, "m => m.querySelectorAll('[data-p]').length")
            check("Julie : pas de choix de personne sur son téléphone", ppl == 0, ppl)
            await page.screenshot(path=f"{OUT}/cp-julie-clavier.png")
            await keypad(page, "5938")
            await page.wait_for_timeout(500)
            check("Julie : surveillance coupée avec son code", await st(s, at, VIGI) == "off")
            await page.wait_for_timeout(1600)
            await click_action(card, page, VIGI, "activer")
            await keypad(page, "5938")
            check("Julie : surveillance remise", await st(s, at, VIGI) == "on")
            await page.wait_for_timeout(1600)
            # changer de code : ancien faux → message, puis tout bon
            await card.evaluate("c => c.shadowRoot.querySelector('[data-g=\"mon_code\"]').click()")
            await page.wait_for_timeout(300)
            await keypad(page, "0000")
            await keypad(page, "61803")
            await keypad(page, "61803")
            msg = await modal_eval(page, "m => m.querySelector('.msg').innerText")
            check("changer de code avec un ancien code faux : refus expliqué", msg and "incorrect" in msg, msg)
            await keypad(page, "5938")
            await keypad(page, "61803")
            await keypad(page, "61804")
            msg = await modal_eval(page, "m => m.querySelector('.msg').innerText")
            check("confirmation différente : on recommence", msg and "différents" in msg, msg)
            await keypad(page, "61803")
            await keypad(page, "61803")
            done = await modal_eval(page, "m => !!m.querySelector('.fait')")
            check("nouveau code enregistré", done)
            r = await ws(s, julie["access_token"], [{"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir",
                                                     "code": "61803"}])
            check("le nouveau code fonctionne", r[0]["result"]["ok"], r)
            await ws(s, julie["access_token"], [{"type": "code_pin/executer", "entity_id": PORTE, "action": "fermer"},
                                                 {"type": "code_pin/definir", "nouveau": "5938", "ancien": "61803"}])
            width = await card.evaluate("c => c.shadowRoot.querySelector('ha-card').scrollWidth <= c.clientWidth + 2")
            check("téléphone : pas de débordement horizontal", width)
            check("Julie : aucune erreur JavaScript", not errors, errors[:3])
            await ctx.close()

            # ── administrateur : gestion et journal ──
            ctx, page, card, errors = await open_card(browser, admin, "admin")
            txt = await card.evaluate("c => c.shadowRoot.querySelector('ha-card').innerText")
            check("admin : mode affiché", "observation" in txt)
            check("admin : entité non protégée signalée", "Protégé par code" in txt)
            check("admin : codes de la famille", "Codes de la famille" in txt and "Tom" in txt, txt[:400])
            rows = await card.evaluate("c => c.shadowRoot.querySelectorAll('tbody tr').length")
            check("admin : journal affiché", rows > 5, rows)
            jt = await card.evaluate("c => c.shadowRoot.querySelector('tbody').innerText")
            check("admin : journal « tablette Salon »", "tablette Salon" in jt, jt[:300])
            # effacer le code de Tom avec le code admin
            await card.evaluate("c => c.shadowRoot.querySelector('[data-g=\"effacer\"][data-p=\"tom\"]').click()")
            await page.wait_for_timeout(300)
            await keypad(page, "86420")
            await page.wait_for_timeout(1500)
            info = await ws(s, at, [{"type": "code_pin/info"}])
            tom = next(x for x in info[0]["result"]["personnes"] if x["cle"] == "tom")
            check("admin : code de Tom effacé", not tom["a_un_code"], tom)
            await page.wait_for_timeout(1500)
            # définir le code de Tom (admin : son code puis le nouveau deux fois)
            await card.evaluate("c => c.shadowRoot.querySelector('[data-g=\"definir\"][data-p=\"tom\"]').click()")
            await page.wait_for_timeout(300)
            await keypad(page, "86420")
            await keypad(page, "27182")
            await keypad(page, "27182")
            info = await ws(s, at, [{"type": "code_pin/info"}])
            tom = next(x for x in info[0]["result"]["personnes"] if x["cle"] == "tom")
            check("admin : code de Tom redéfini", tom["a_un_code"], tom)
            check("admin : aucune erreur JavaScript", not errors, errors[:3])
            await ctx.close()
            await browser.close()
    print(f"\n{sum(RESULTS)}/{len(RESULTS)}")


asyncio.run(main())
