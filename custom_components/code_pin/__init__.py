"""Codes d'accès : code personnel pour les actions protégées (portes, alarmes, vigilance…), tablettes murales,
synchronisation des codes avec l'autre Home Assistant."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import CARD_URL, DOMAIN, STORE_CODES, STORE_JOURNAL, STORE_VERSION
from .http import ExecView, SyncView
from .manager import CodePin

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_PATH = Path(__file__).parent / "frontend" / "code-pin-card.js"

type CodePinConfigEntry = ConfigEntry[CodePin]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    websocket.async_register(hass)
    hass.http.register_view(SyncView())
    hass.http.register_view(ExecView())
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_PATH), True)])
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CodePinConfigEntry) -> bool:
    await _register_card(hass)
    mgr = CodePin(hass, entry)
    await mgr.async_start()
    entry.runtime_data = mgr
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CodePinConfigEntry) -> bool:
    await entry.runtime_data.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: CodePinConfigEntry) -> None:
    """Désinstallation : codes et journal effacés, carte retirée des ressources."""
    for key in (STORE_CODES, STORE_JOURNAL):
        await Store(hass, STORE_VERSION, key).async_remove()
    resources = getattr(hass.data.get(LOVELACE_DATA), "resources", None)
    if resources is not None and getattr(hass.data.get(LOVELACE_DATA), "resource_mode", None) == "storage":
        try:
            await resources.async_get_info()
            for r in list(resources.async_items()):
                if str(r.get("url", "")).split("?")[0] == CARD_URL:
                    await resources.async_delete_item(r["id"])
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Ressource de la carte non retirée")


async def _options_changed(hass: HomeAssistant, entry: CodePinConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _register_card(hass: HomeAssistant) -> None:
    """Déclare la carte comme ressource de tableau de bord (rechargée dès qu'elle change)."""
    path = CARD_PATH
    url = f"{CARD_URL}?v={await hass.async_add_executor_job(_file_hash, path)}"
    lovelace = hass.data.get(LOVELACE_DATA)
    resources = getattr(lovelace, "resources", None)
    if lovelace is not None and lovelace.resource_mode == "storage" and resources is not None:
        try:
            await resources.async_get_info()
            mine = [r for r in resources.async_items() if str(r.get("url", "")).split("?")[0] == CARD_URL]
            if not mine:
                await resources.async_create_item({"res_type": "module", "url": url})
            else:
                if mine[0].get("url") != url:
                    await resources.async_update_item(mine[0]["id"], {"res_type": "module", "url": url})
                for extra in mine[1:]:
                    await resources.async_delete_item(extra["id"])
            return
        except Exception:
            _LOGGER.exception("Ressource de la carte non enregistrée ; repli sur le chargement direct")
    if hass.data.setdefault(DOMAIN, {}).get("js_added"):
        return
    try:
        frontend.add_extra_js_url(hass, url)
        hass.data[DOMAIN]["js_added"] = True
    except KeyError:
        _LOGGER.debug("Interface non chargée : carte non injectée")


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"
