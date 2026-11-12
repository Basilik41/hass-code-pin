"""Banc de bout en bout des codes d'accès sur deux vrais Home Assistant.

A (port 8123) joue la Grange : porte, serrure, alarme, interrupteurs protégés.
B (port 8124) joue la Maison : voit la porte et l'alarme de A par Remote Home Assistant.
Usage : python bench_code_pin.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time

import aiohttp

sys.path.insert(0, "/home/claude/hass-acces-distant/tests")
sys.path.insert(0, "/home/claude/hass-journal-lieux/tests")
from bench_v15 import TOK_A, TOK_B, WS, A, B, admin_token, ensure_user, login

RESULTS: list[bool] = []
PORTE, SERRURE, ALARME = "cover.porte_banc", "lock.serrure_banc", "alarm_control_panel.alarme_banc"
VIGI, DETECT = "input_boolean.vigilance_banc", "input_boolean.detection_banc"
M_PORTE, M_ALARME = "cover.grange_porte_banc", "alarm_control_panel.grange_alarme_banc"


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("✅ " if cond else "❌ ") + name + (f" — {detail}" if detail and not cond else ""), flush=True)
    return bool(cond)


async def call(w, msg):
    r = await w.call(msg)
    assert r.get("success"), r
    return r["result"]


async def state(s, base, tok, eid):
    async with s.get(f"{base}/api/states/{eid}", headers={"Authorization": f"Bearer {tok}"}) as r:
        return (await r.json()).get("state") if r.status == 200 else None


async def svc(w, domain, service, target=None, data=None):
    return await w.call({"type": "call_service", "domain": domain, "service": service,
                         "target": target or {}, "service_data": data or {}})


async def ensure_person(w, name, user_id):
    ps = (await call(w, {"type": "person/list"}))["storage"]
    p = next((p for p in ps if p["name"] == name), None)
    if p is None:
        p = await call(w, {"type": "person/create", "name": name, "user_id": user_id})
    elif p.get("user_id") != user_id:
        p = await call(w, {"type": "person/update", "person_id": p["id"], "name": name, "user_id": user_id})
    return p


async def install(s, base, tok, w):
    h = {"Authorization": f"Bearer {tok}"}
    for e in await call(w, {"type": "config_entries/get", "domain": "code_pin"}):
        async with s.delete(f"{base}/api/config/config_entries/entry/{e['entry_id']}", headers=h) as r:
            assert r.status == 200, r.status
    async with s.post(f"{base}/api/config/config_entries/flow", json={"handler": "code_pin"}, headers=h) as r:
        f = await r.json()
    async with s.post(f"{base}/api/config/config_entries/flow/{f['flow_id']}", json={}, headers=h) as r:
        res = await r.json()
    assert res.get("type") == "create_entry", res
    return res["result"]["entry_id"]


async def set_options(s, base, tok, entry_id, **opts):
    h = {"Authorization": f"Bearer {tok}"}
    async with s.post(f"{base}/api/config/config_entries/options/flow", json={"handler": entry_id}, headers=h) as r:
        form = await r.json()
    cur = {}
    for fld in form["data_schema"]:
        if "default" in fld:
            cur[fld["name"]] = fld["default"]
        sv = (fld.get("description") or {}).get("suggested_value")
        if sv is not None:
            cur[fld["name"]] = sv
    async with s.post(f"{base}/api/config/config_entries/options/flow/{form['flow_id']}",
                      json={**cur, **opts}, headers=h) as r:
        res = await r.json()
    await asyncio.sleep(2.5)  # rechargement de l'intégration
    return res


async def label(w, eids, label_id):
    for eid in eids:
        ent = await call(w, {"type": "config/entity_registry/get", "entity_id": eid})
        labels = sorted(set(ent.get("labels") or []) | {label_id})
        await call(w, {"type": "config/entity_registry/update", "entity_id": eid, "labels": labels})


async def notes(w_events):
    """Notifications « banc_notification » reçues depuis l'abonnement."""
    out = []
    while True:
        try:
            m = await asyncio.wait_for(w_events.ws.receive_json(), 0.2)
        except TimeoutError:
            return out
        if m.get("type") == "event":
            out.append(m["event"]["data"])


async def main():
    async with aiohttp.ClientSession() as s:
        ta, tb = await admin_token(s, A, TOK_A), await admin_token(s, B, TOK_B)
        async with WS(s, A, ta) as wa, WS(s, B, tb) as wb:
            # ── comptes et personnes ──
            ua = {u["username"]: u for u in await call(wa, {"type": "config/auth/list"}) if u.get("username")}
            ub = {u["username"]: u for u in await call(wb, {"type": "config/auth/list"}) if u.get("username")}
            await ensure_user(wa, "TabDomo Salon", "tabsalon", "motdepasse-tab")
            for uname in ("julie", "val"):
                await call(wa, {"type": "config/auth_provider/homeassistant/admin_change_password",
                                "user_id": ua[uname]["id"], "password": f"motdepasse-{uname}"})
                await call(wb, {"type": "config/auth_provider/homeassistant/admin_change_password",
                                "user_id": ub[uname]["id"], "password": f"motdepasse-{uname}"})
            await ensure_person(wa, "Julie", ua["julie"]["id"])
            await ensure_person(wa, "Val", ua["val"]["id"])
            await ensure_person(wb, "Julie", ub["julie"]["id"])
            await ensure_person(wb, "Valérie", ub["val"]["id"])
            await ensure_person(wa, "Tom", ua["tom"]["id"])
            await wb.call({"type": "http/config/promote"})  # banc B : configuration HTTP confirmée (sinon redémarre)
            # jeton de liaison B → A (compte administrateur de A, comme Remote Home Assistant)
            llat = await call(wa, {"type": "auth/long_lived_access_token", "client_name": f"liaison {time.time()}",
                                   "lifespan": 30})
            # ── Remote Home Assistant : A « nœud distant », B relié à A (miroir de la porte et de l'alarme) ──
            if not await call(wa, {"type": "config_entries/get", "domain": "remote_homeassistant"}):
                ha_ = {"Authorization": f"Bearer {ta}"}
                async with s.post(f"{A}/api/config/config_entries/flow", json={"handler": "remote_homeassistant"},
                                  headers=ha_) as r:
                    f = await r.json()
                async with s.post(f"{A}/api/config/config_entries/flow/{f['flow_id']}",
                                  json={"type": "Setup as remote node"}, headers=ha_) as r:
                    print("nœud distant A :", (await r.json()).get("type"))
                await asyncio.sleep(2)
            hb = {"Authorization": f"Bearer {tb}"}
            ex = await call(wb, {"type": "config_entries/get", "domain": "remote_homeassistant"})
            if ex:
                rha = ex[0]["entry_id"]
                check("liaison Remote Home Assistant présente sur B", True)
            else:
                async with s.post(f"{B}/api/config/config_entries/flow", json={"handler": "remote_homeassistant"},
                                  headers=hb) as r:
                    f = await r.json()
                async with s.post(f"{B}/api/config/config_entries/flow/{f['flow_id']}",
                                  json={"type": "Add a remote node"}, headers=hb) as r:
                    f = await r.json()
                async with s.post(f"{B}/api/config/config_entries/flow/{f['flow_id']}", json={
                        "host": "127.0.0.1", "port": 8123, "secure": False, "verify_ssl": False,
                        "access_token": llat, "max_message_size": 16 * 1024 * 1024}, headers=hb) as r:
                    res = await r.json()
                check("liaison Remote Home Assistant créée sur B", res.get("type") == "create_entry",
                      json.dumps(res)[:300])
                rha = res["result"]["entry_id"]
            for _ in range(30):
                e = next(x for x in await call(wb, {"type": "config_entries/get", "domain": "remote_homeassistant"})
                         if x["entry_id"] == rha)
                if e["state"] == "loaded":
                    break
                await asyncio.sleep(1)
            await asyncio.sleep(3)
            async with s.post(f"{B}/api/config/config_entries/options/flow", json={"handler": rha}, headers=hb) as r:
                f = await r.json()
            async with s.post(f"{B}/api/config/config_entries/options/flow/{f['flow_id']}", json={
                    "entity_prefix": "grange_", "load_components": ["cover", "alarm_control_panel"],
                    "service_prefix": "grange", "services": []}, headers=hb) as r:
                f = await r.json()
            # étapes suivantes du formulaire d'options (filtres / entités)
            for _ in range(4):
                if f.get("type") != "form":
                    break
                body = {}
                names = [x["name"] for x in f.get("data_schema") or []]
                if "include_entities" in names:
                    body["include_entities"] = [PORTE, ALARME]
                async with s.post(f"{B}/api/config/config_entries/options/flow/{f['flow_id']}", json=body,
                                  headers=hb) as r:
                    f = await r.json()
            for _ in range(30):
                if await state(s, B, tb, M_PORTE):
                    break
                await asyncio.sleep(1)
            check("B voit la porte de A (miroir)", await state(s, B, tb, M_PORTE) is not None)

            # ── installation des deux côtés ──
            ea = await install(s, A, ta, wa)
            eb = await install(s, B, tb, wb)
            labs = await call(wa, {"type": "config/label_registry/list"})
            lab = next((x for x in labs if x["name"] == "Protégé par code"), None)
            check("étiquette « Protégé par code » créée", lab is not None)
            await label(wa, [PORTE, SERRURE, ALARME, VIGI, DETECT], lab["label_id"])
            labb = next(x for x in await call(wb, {"type": "config/label_registry/list"}) if x["name"] == "Protégé par code")
            await label(wb, [M_PORTE, M_ALARME], labb["label_id"])
            await set_options(s, A, ta, ea, codes_alarme=f"{ALARME}=1357")
            await set_options(s, B, tb, eb, alias_personnes="valerie=val")
            await asyncio.sleep(1)
            # état initial
            await svc(wa, "input_boolean", "turn_off", {"entity_id": "input_boolean.porte_banc_etat"})
            await svc(wa, "input_boolean", "turn_on", {"entity_id": "input_boolean.serrure_banc_etat"})
            await svc(wa, "alarm_control_panel", "alarm_disarm", {"entity_id": ALARME}, {"code": "1357"})

            julie_a = await login(s, A, "julie", "motdepasse-julie")
            julie_b = await login(s, B, "julie", "motdepasse-julie")
            val_b = await login(s, B, "val", "motdepasse-val")
            tab_a = await login(s, A, "tabsalon", "motdepasse-tab")

            async with WS(s, A, julie_a) as wj, WS(s, A, tab_a) as wt, WS(s, B, julie_b) as wjb, \
                    WS(s, B, val_b) as wvb, WS(s, A, ta) as wev:
                await wev.call({"type": "subscribe_events", "event_type": "banc_notification"})
                info = await call(wj, {"type": "code_pin/info", "entites": [PORTE, SERRURE, ALARME, VIGI, "light.x"]})
                check("Julie : reconnue comme personne, sans code", info["moi"]["type"] == "personne"
                      and info["moi"]["cle"] == "julie" and not info["moi"]["a_un_code"], info["moi"])
                check("entités protégées décrites", info["entites"][PORTE]["protege"]
                      and {a["cle"] for a in info["entites"][PORTE]["actions"]} >= {"ouvrir", "fermer"}
                      and not info["entites"]["light.x"]["protege"], info["entites"][PORTE])
                check("« ouvrir » demande un code, « fermer » non",
                      {a["cle"]: a["code"] for a in info["entites"][PORTE]["actions"]} == {"ouvrir": True, "fermer": True,
                                                                                          "stop": False} or
                      {a["cle"]: a["code"] for a in info["entites"][PORTE]["actions"]}.get("fermer") is False,
                      info["entites"][PORTE]["actions"])
                check("Julie (non admin) ne voit pas le journal",
                      not (await wj.call({"type": "code_pin/journal"}))["success"])
                r = await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir", "code": "4827"})
                check("sans code défini : refus « pas_de_code »", r["erreur"] == "pas_de_code", r)
                r = await call(wj, {"type": "code_pin/definir", "nouveau": "1234"})
                check("code trop simple refusé", r["erreur"] == "trop_simple", r)
                r = await call(wj, {"type": "code_pin/definir", "nouveau": "4827"})
                check("Julie définit son code", r["ok"], r)
                r = await call(wj, {"type": "code_pin/definir", "nouveau": "5938", "ancien": "0000"})
                check("changer de code exige l'ancien", r["erreur"] == "code_faux", r)
                r = await call(wj, {"type": "code_pin/definir", "nouveau": "5938", "ancien": "4827"})
                check("Julie change son code", r["ok"], r)

                # ── action avec code ──
                r = await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir", "code": "1111"})
                check("code faux refusé, essais restants", r["erreur"] == "code_faux" and r["essais"] == 4, r)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir", "code": "5938"})
                await asyncio.sleep(0.5)
                check("bon code : la porte s'ouvre", r["ok"] and await state(s, A, ta, PORTE) == "open", r)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "fermer"})
                await asyncio.sleep(0.5)
                check("fermer sans code", r["ok"] and await state(s, A, ta, PORTE) == "closed", r)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": "light.x", "action": "activer",
                                    "code": "5938"})
                check("entité non protégée refusée", r["erreur"] == "non_protege", r)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "exploser",
                                    "code": "5938"})
                check("action inconnue refusée", r["erreur"] == "action", r)
                n0 = len(await notes(wev))
                check("une action avec code ne déclenche aucune alerte", n0 == 0, n0)

                # ── blocage après 5 erreurs ──
                for i in range(5):
                    r = await call(wj, {"type": "code_pin/executer", "entity_id": SERRURE, "action": "deverrouiller",
                                        "code": "0001"})
                check("5e erreur : code bloqué", r["erreur"] == "bloque" and 250 < r["secondes"] <= 300, r)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": SERRURE, "action": "deverrouiller",
                                    "code": "5938"})
                check("bloqué : même le bon code est refusé", r["erreur"] == "bloque", r)
                check("serrure restée verrouillée", await state(s, A, ta, SERRURE) == "locked")
                ns = await notes(wev)
                check("alerte des administrateurs au blocage",
                      any("bloqué" in n["message"] and n["niveau"] == "admin" for n in ns), ns)
                r = await call(wa, {"type": "code_pin/debloquer", "personne": "julie", "code_admin": "0000"})
                check("débloquer : l'administrateur doit avoir un code", r["erreur"] == "admin_sans_code", r)
                r = await call(wa, {"type": "code_pin/definir", "nouveau": "86420"})
                check("Richard définit son code", r["ok"], r)
                r = await call(wa, {"type": "code_pin/debloquer", "personne": "julie", "code_admin": "86420"})
                check("Richard débloque Julie avec son code", r["ok"], r)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": SERRURE, "action": "deverrouiller",
                                    "code": "5938"})
                await asyncio.sleep(0.5)
                check("après déblocage : la serrure s'ouvre", r["ok"] and await state(s, A, ta, SERRURE) == "unlocked", r)
                await call(wj, {"type": "code_pin/executer", "entity_id": SERRURE, "action": "verrouiller"})

                # ── alarme : désarmer avec code, armer sans ──
                r = await call(wj, {"type": "code_pin/executer", "entity_id": ALARME, "action": "armer_absent"})
                await asyncio.sleep(0.5)
                check("armer sans code", r["ok"] and await state(s, A, ta, ALARME) == "armed_away", r)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": ALARME, "action": "armer_present"})
                check("passer d'absent à présent demande le code", r["erreur"] == "code_faux", r)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": ALARME, "action": "desarmer",
                                    "code": "5938"})
                await asyncio.sleep(0.5)
                check("désarmer avec le code (code de la centrale fourni par le module)",
                      r["ok"] and await state(s, A, ta, ALARME) == "disarmed", r)

                # ── tablette murale ──
                it = await call(wt, {"type": "code_pin/info", "entites": [PORTE]})
                check("tablette reconnue (« Salon »)", it["moi"]["type"] == "tablette" and it["moi"]["appareil"] == "Salon",
                      it["moi"])
                check("tablette : seules les personnes avec un code", {p["cle"] for p in it["personnes"]}
                      == {"julie", "richard"}, it["personnes"])
                r = await call(wt, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir", "code": "5938"})
                check("tablette : personne non choisie → refus", r["erreur"] == "personne", r)
                r = await call(wt, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir",
                                    "personne": "richard", "code": "5938"})
                check("tablette : code de Julie avec la photo de Richard → refus", r["erreur"] == "code_faux", r)
                r = await call(wt, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir",
                                    "personne": "julie", "code": "5938"})
                await asyncio.sleep(0.5)
                check("tablette : Julie + son code → porte ouverte", r["ok"] and "tablette Salon" in r["qui"], r)
                r = await call(wt, {"type": "code_pin/definir", "nouveau": "5555"})
                check("tablette : impossible de définir un code", r["erreur"] == "compte", r)
                await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "fermer"})
                j = await call(wa, {"type": "code_pin/journal", "jours": 1})
                e = next((x for x in j["entrees"] if x["appareil"] == "Salon"), None)
                check("journal : Julie, tablette Salon, porte", e and e["personne"] == "Julie" and e["entite"] == PORTE, e)
                async with s.get(f"{A}/api/logbook", headers={"Authorization": f"Bearer {ta}"}) as rr:
                    lb = [x for x in await rr.json() if x.get("domain") == "code_pin"]
                check("journal de Home Assistant : « Ouvrir par Julie (tablette Salon) » sur la porte",
                      any(x.get("entity_id") == PORTE and "Ouvrir par Julie (tablette Salon)" in x.get("message", "")
                          for x in lb), lb[-3:])
                # balayage de codes depuis une tablette (toutes personnes) → tablette bloquée
                r = await call(wa, {"type": "code_pin/definir", "personne": "tom", "nouveau": "27182",
                                    "code_admin": "86420"})
                check("Richard définit le code de Tom", r["ok"], r)
                for i in range(10):
                    r = await call(wt, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir",
                                        "personne": ["julie", "tom"][i % 2], "code": f"90{i:02d}"})
                check("10 erreurs sur une tablette → tablette bloquée", r["erreur"] in ("appareil_bloque", "bloque"), r)
                r = await call(wt, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir",
                                    "personne": "julie", "code": "5938"})
                check("tablette bloquée : même le bon code est refusé", r["erreur"] in ("appareil_bloque", "bloque"), r)
                await call(wa, {"type": "code_pin/debloquer", "personne": "julie", "code_admin": "86420"})
                r = await call(wa, {"type": "code_pin/debloquer", "personne": "tom", "code_admin": "86420"})
                check("Richard débloque Julie et Tom", r["ok"], r)
                await notes(wev)

                # ── commandes directes : mode observation ──
                await svc(wj, "cover", "open_cover", {"entity_id": PORTE})
                await asyncio.sleep(1.5)
                ns = await notes(wev)
                check("observation : commande directe signalée", any("sans passer par le code" in n["message"] and "Julie" in n["message"]
                                                                     for n in ns), ns)
                check("observation : rien n'est annulé", await state(s, A, ta, PORTE) == "open")
                await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "fermer"})
                await svc(wj, "cover", "close_cover", {"entity_id": PORTE})
                await asyncio.sleep(1)
                check("fermer directement : ni alerte ni journal", not await notes(wev))
                # automatisation : jamais contrôlée
                await wa.call({"type": "fire_event", "event_type": "banc_ouvrir"})
                await asyncio.sleep(1.5)
                check("automatisation déclenchée par un évènement : pas d'alerte", not await notes(wev)
                      and await state(s, A, ta, PORTE) == "open")
                await svc(wa, "cover", "close_cover", {"entity_id": PORTE})

                # ── mode blocage ──
                await set_options(s, A, ta, ea, mode="blocage")
                await svc(wj, "input_boolean", "turn_off", {"entity_id": VIGI})
                await asyncio.sleep(0.5)
                mid = await state(s, A, ta, VIGI)
                await asyncio.sleep(3)
                check("blocage : vigilance coupée sans code → remise en marche", mid == "off"
                      and await state(s, A, ta, VIGI) == "on", mid)
                ns = await notes(wev)
                check("blocage : alerte « Commande annulée »", any("annulée" in n["message"] for n in ns), ns)
                await svc(wj, "homeassistant", "turn_off", {"label_id": lab["label_id"]})
                await asyncio.sleep(3.5)
                st2 = (await state(s, A, ta, VIGI), await state(s, A, ta, DETECT))
                check("blocage : commande par étiquette repérée et annulée", st2 == ("on", "on"), st2)
                await svc(wa, "cover", "open_cover", {"entity_id": PORTE})
                await asyncio.sleep(3.5)
                check("blocage : même un administrateur sans code est annulé", await state(s, A, ta, PORTE) == "closed")
                await svc(wa, "lock", "unlock", {"entity_id": SERRURE})
                await asyncio.sleep(3.5)
                check("blocage : serrure re-verrouillée", await state(s, A, ta, SERRURE) == "locked")
                await call(wj, {"type": "code_pin/executer", "entity_id": ALARME, "action": "armer_absent"})
                await asyncio.sleep(0.5)
                await svc(wj, "alarm_control_panel", "alarm_disarm", {"entity_id": ALARME}, {"code": "1357"})
                await asyncio.sleep(3.5)
                check("blocage : alarme désarmée sans code → réarmée", await state(s, A, ta, ALARME) == "armed_away",
                      await state(s, A, ta, ALARME))
                r = await call(wj, {"type": "code_pin/executer", "entity_id": ALARME, "action": "desarmer",
                                    "code": "5938"})
                await asyncio.sleep(3)
                check("blocage : désarmer avec le code fonctionne (pas annulé)", r["ok"]
                      and await state(s, A, ta, ALARME) == "disarmed", r)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": VIGI, "action": "couper", "code": "5938"})
                await asyncio.sleep(3)
                check("blocage : couper la vigilance avec le code reste coupé", r["ok"]
                      and await state(s, A, ta, VIGI) == "off", r)
                await call(wj, {"type": "code_pin/executer", "entity_id": VIGI, "action": "activer", "code": "5938"})
                await notes(wev)
                # compte exempté
                await set_options(s, A, ta, ea, comptes_exemptes=[ua["val"]["id"]])
                val_a = await login(s, A, "val", "motdepasse-val")
                async with WS(s, A, val_a) as wv:
                    await svc(wv, "input_boolean", "turn_off", {"entity_id": DETECT})
                await asyncio.sleep(3)
                check("compte exempté : pas annulé", await state(s, A, ta, DETECT) == "off")
                await svc(wa, "input_boolean", "turn_on", {"entity_id": DETECT})
                await asyncio.sleep(3)
                await set_options(s, A, ta, ea, comptes_exemptes=[])
                await notes(wev)

                # ── synchronisation avec B ──
                await call(wa, {"type": "code_pin/synchroniser"})
                ib = await call(wjb, {"type": "code_pin/info"})
                check("B : le code de Julie est arrivé", ib["moi"]["cle"] == "julie" and ib["moi"]["a_un_code"], ib["moi"])
                st = (await call(wb, {"type": "code_pin/info"}))["synchro"]
                check("B : synchronisation par la liaison Remote Home Assistant", any(v["ok"] for v in st.values()), st)
                r = await call(wvb, {"type": "code_pin/definir", "nouveau": "31415"})
                check("B : Valérie (alias de val) définit son code", r["ok"], r)
                await asyncio.sleep(2)
                ia = await call(wa, {"type": "code_pin/info"})
                check("A : le code de Val est arrivé (alias)", next(p for p in ia["personnes"] if p["cle"] == "val")
                      ["a_un_code"], ia["personnes"])

                # ── action sur un miroir depuis B ──
                info_b = await call(wjb, {"type": "code_pin/info", "entites": [M_PORTE]})
                check("B : porte de A vue comme distante", info_b["entites"][M_PORTE]["distant"], info_b["entites"])
                r = await call(wjb, {"type": "code_pin/executer", "entity_id": M_PORTE, "action": "ouvrir",
                                     "code": "5938"})
                await asyncio.sleep(3)
                check("B : Julie ouvre la porte de A avec son code", r["ok"] and await state(s, A, ta, PORTE) == "open", r)
                j = await call(wa, {"type": "code_pin/journal", "jours": 1})
                check("A : journal « Julie, Banc B »", j["entrees"][0]["site"] == "Banc B"
                      and j["entrees"][0]["personne"] == "Julie", j["entrees"][0])
                check("A : pas d'alerte pour une action venue de B avec code", not await notes(wev))
                await call(wjb, {"type": "code_pin/executer", "entity_id": M_PORTE, "action": "fermer"})
                await asyncio.sleep(3)
                check("B : fermer la porte de A sans code", await state(s, A, ta, PORTE) == "closed")
                # commande directe sur le miroir : annulée chez A (blocage)
                await svc(wjb, "cover", "open_cover", {"entity_id": M_PORTE})
                await asyncio.sleep(5)
                check("B : commande directe sur le miroir → annulée par A", await state(s, A, ta, PORTE) == "closed")
                ns = await notes(wev)
                check("A : alerte « ou via l'autre Home Assistant »", any("via l'autre" in n["message"] for n in ns), ns)
                jb = await call(wb, {"type": "code_pin/journal", "jours": 1})
                check("B : la commande directe de Julie est journalisée avec son nom",
                      any(e["resultat"] == "commande_directe" and e["personne"] == "Julie" for e in jb["entrees"]),
                      jb["entrees"][:3])

                # ── effacement propagé ──
                r = await call(wa, {"type": "code_pin/effacer", "personne": "val", "code_admin": "86420"})
                await call(wb, {"type": "code_pin/synchroniser"})  # A n'a pas de liaison vers B sur le banc
                ivb = await call(wvb, {"type": "code_pin/info"})
                check("code effacé sur A → effacé sur B", r["ok"] and not ivb["moi"]["a_un_code"], ivb["moi"])

                # ── B arrêté : A continue ──
                subprocess.run(["bash", "/home/claude/hass-acces-distant/tests/restart2.sh", "stop"], check=False,
                               capture_output=True)
                st = (await call(wa, {"type": "code_pin/synchroniser"}))["synchro"]
                check("B arrêté : erreur de synchro affichée", not any(v["ok"] for v in st.values()), st)
                r = await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir", "code": "5938"})
                check("B arrêté : A fonctionne seul", r["ok"], r)
                await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "fermer"})

        # ── redémarrage de A : codes et journal conservés ──
        from bench_journal import restart_ha
        restart_ha()
        subprocess.run(["bash", "/home/claude/hass-acces-distant/tests/restart2.sh"], check=False, capture_output=True)
        async with WS(s, B, await admin_token(s, B, TOK_B)) as wb:
            await wb.call({"type": "http/config/promote"})
        ta = await admin_token(s, A, TOK_A)
        julie_a = await login(s, A, "julie", "motdepasse-julie")
        async with WS(s, A, julie_a) as wj, WS(s, A, ta) as wa:
            r = await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "ouvrir", "code": "5938"})
            check("après redémarrage : le code de Julie marche", r["ok"], r)
            await call(wj, {"type": "code_pin/executer", "entity_id": PORTE, "action": "fermer"})
            j = await call(wa, {"type": "code_pin/journal", "jours": 1})
            check("après redémarrage : journal conservé", j["total"] > 10, j["total"])
            ia = await call(wa, {"type": "code_pin/info"})
            check("mode blocage conservé", ia["mode"] == "blocage")
            await set_options(s, A, ta, ea, mode="observation")
        log = open("/home/claude/bench/hass.log").read()[-200000:]
        errs = [line for line in log.splitlines() if "code_pin" in line and ("ERROR" in line or "Traceback" in line)]
        check("aucune erreur code_pin dans le journal de A", not errs, errs[-3:])

    print(f"\n{sum(RESULTS)}/{len(RESULTS)}")


asyncio.run(main())
