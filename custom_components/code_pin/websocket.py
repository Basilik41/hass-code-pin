"""API de la carte. Les refus métier (code faux, bloqué…) sont des résultats {ok: false}, pas des erreurs."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from functools import wraps

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PIN_MAX, PIN_MIN, VERSION, VISITOR_KEY
from .manager import CodeError, CodePin
from .pin import has_code

_PIN = vol.All(cv.string, vol.Length(max=32))


def _mgr(hass: HomeAssistant) -> CodePin | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        m = getattr(entry, "runtime_data", None)
        if isinstance(m, CodePin):
            return m
    return None


def _handler(admin: bool = False) -> Callable:
    def deco(fn: Callable[..., Awaitable[dict | None]]) -> Callable[..., Awaitable[None]]:
        @wraps(fn)
        async def wrapper(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict) -> None:
            if admin and not (conn.user and conn.user.is_admin):
                conn.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, "Réservé aux administrateurs")
                return
            m = _mgr(hass)
            if m is None:
                conn.send_error(msg["id"], "not_loaded", "Module Codes d'accès non configuré")
                return
            try:
                res = await fn(hass, conn, msg, m)
            except CodeError as err:
                conn.send_result(msg["id"], {"ok": False, "erreur": err.code, "message": err.message, **err.extra})
                return
            conn.send_result(msg["id"], {"ok": True, **(res or {})})
        return wrapper
    return deco


@callback
def async_register(hass: HomeAssistant) -> None:
    for cmd in (ws_info, ws_executer, ws_definir, ws_effacer, ws_debloquer, ws_journal, ws_synchroniser):
        websocket_api.async_register_command(hass, cmd)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/info",
    vol.Optional("entites", default=[]): vol.All([cv.entity_id], vol.Length(max=200)),
})
@websocket_api.async_response
@_handler()
async def ws_info(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict, m: CodePin) -> dict:
    who = m.identify(conn.user)
    admin = bool(conn.user and conn.user.is_admin)
    persons = [{"cle": p["key"], "nom": p["nom"], "photo": p["photo"], "entity_id": p["entity_id"],
                "a_un_code": has_code(m.codes.get(p["key"])), "bloque": int(m.locked_for(p["key"])),
                "moi": p["key"] == who.key} for p in m.persons()]
    res = {
        "version": VERSION, "mode": m.mode, "pin_min": PIN_MIN, "pin_max": PIN_MAX,
        "moi": {"type": who.kind, "cle": who.key, "nom": who.name, "appareil": who.device, "admin": admin,
                "a_un_code": has_code(m.codes.get(who.key or ""))},
        # sur une tablette, on ne montre que les personnes qui ont un code
        "personnes": [p for p in persons if p["a_un_code"] or who.kind != "tablette"] + (
            [{"cle": VISITOR_KEY, "nom": "Visiteur", "photo": None, "entity_id": None, "a_un_code": True,
              "bloque": int(m.locked_for(VISITOR_KEY)), "moi": False, "visiteur": True}]
            if who.kind == "tablette" and m.visitor_codes() else []),
        "entites": {e: m.describe(e) for e in msg["entites"]},
    }
    if admin:
        res["synchro"] = m.sync_status
        res["protegees"] = sorted(m._protected)
    return res


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/executer",
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("action"): vol.All(cv.string, vol.Length(max=32)),
    vol.Optional("personne"): vol.All(cv.string, vol.Length(max=64)),
    vol.Optional("code"): _PIN,
})
@websocket_api.async_response
@_handler()
async def ws_executer(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict, m: CodePin) -> dict:
    return await m.execute(conn.user, msg["entity_id"], msg["action"], msg.get("personne"), msg.get("code"))


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/definir",
    vol.Required("nouveau"): _PIN,
    vol.Optional("ancien"): _PIN,
    vol.Optional("personne"): vol.All(cv.string, vol.Length(max=64)),
    vol.Optional("code_admin"): _PIN,
})
@websocket_api.async_response
@_handler()
async def ws_definir(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict, m: CodePin) -> None:
    await m.set_code(conn.user, msg["nouveau"], msg.get("ancien"), msg.get("personne"), msg.get("code_admin"))


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/effacer",
    vol.Required("personne"): vol.All(cv.string, vol.Length(max=64)),
    vol.Required("code_admin"): _PIN,
})
@websocket_api.async_response
@_handler(admin=True)
async def ws_effacer(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict, m: CodePin) -> None:
    await m.reset_code(conn.user, msg["personne"], msg["code_admin"])


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/debloquer",
    vol.Required("personne"): vol.All(cv.string, vol.Length(max=64)),
    vol.Required("code_admin"): _PIN,
})
@websocket_api.async_response
@_handler(admin=True)
async def ws_debloquer(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict, m: CodePin) -> None:
    await m.unlock(conn.user, msg["personne"], msg["code_admin"])


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/journal",
    vol.Optional("jours", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=366)),
    vol.Optional("limite", default=300): vol.All(vol.Coerce(int), vol.Range(min=1, max=3000)),
})
@websocket_api.async_response
@_handler(admin=True)
async def ws_journal(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict, m: CodePin) -> dict:
    since = time.time() - msg["jours"] * 86400
    rows = [e for e in m.journal if e["t"] >= since]
    return {"entrees": rows[-msg["limite"]:][::-1], "total": len(rows)}


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/synchroniser"})
@websocket_api.async_response
@_handler(admin=True)
async def ws_synchroniser(hass: HomeAssistant, conn: websocket_api.ActiveConnection, msg: dict, m: CodePin) -> dict:
    await m.sync_all()
    return {"synchro": m.sync_status}
