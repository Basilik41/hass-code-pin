"""Points d'accès pour l'autre Home Assistant : jeton d'un administrateur ou d'un compte de liaison autorisé."""
from __future__ import annotations

from http import HTTPStatus

import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import KEY_HASS, KEY_HASS_USER, HomeAssistantView
from homeassistant.helpers import config_validation as cv

from .const import API_EXEC, API_SYNC, DOMAIN
from .manager import CodeError

_SYNC = vol.Schema({
    vol.Required("personnes"): vol.All(dict, vol.Length(max=200)),
    vol.Optional("instance"): cv.string,
}, extra=vol.REMOVE_EXTRA)
_EXEC = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("action"): vol.All(cv.string, vol.Length(max=32)),
    vol.Optional("personne"): vol.Any(None, vol.All(cv.string, vol.Length(max=64))),
    vol.Optional("nom"): vol.Any(None, vol.All(cv.string, vol.Length(max=100))),
    vol.Optional("appareil"): vol.Any(None, vol.All(cv.string, vol.Length(max=100))),
    vol.Optional("site"): vol.Any(None, vol.All(cv.string, vol.Length(max=100))),
}, extra=vol.REMOVE_EXTRA)


def _mgr(request: web.Request):
    from .websocket import _mgr as get
    return get(request.app[KEY_HASS])


class _Base(HomeAssistantView):
    requires_auth = True

    async def _body(self, request: web.Request, schema: vol.Schema):
        user = request[KEY_HASS_USER]
        m = _mgr(request)
        if m is None:
            return None, self.json({"code": "absent", "message": "Module non configuré"}, HTTPStatus.NOT_FOUND)
        if not m.peer_allowed(user):
            return None, self.json({"code": "droits", "message": "Compte de liaison ou administrateur requis"},
                                   HTTPStatus.FORBIDDEN)
        try:
            data = schema(await request.json())
        except (ValueError, vol.Invalid) as err:
            return None, self.json({"code": "requete", "message": str(err)[:200]}, HTTPStatus.BAD_REQUEST)
        return (m, user, data), None


class SyncView(_Base):
    url = API_SYNC
    name = f"api:{DOMAIN}:sync"

    async def post(self, request: web.Request) -> web.Response:
        ok, err = await self._body(request, _SYNC)
        if err:
            return err
        m, user, data = ok
        return self.json(await m.sync_from_peer(user, data["personnes"]))


class ExecView(_Base):
    url = API_EXEC
    name = f"api:{DOMAIN}:executer"

    async def post(self, request: web.Request) -> web.Response:
        ok, err = await self._body(request, _EXEC)
        if err:
            return err
        m, user, data = ok
        try:
            return self.json(await m.exec_from_peer(user, data))
        except CodeError as e:
            return self.json({"code": e.code, "message": e.message}, HTTPStatus.CONFLICT)
