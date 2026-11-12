"""Journal de Home Assistant : « Porte écurie — Ouvrir par Julie (tablette Salon) »."""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_ENTITY_ID, LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_ACTION

_RESULT = {"ok": "", "refuse": " — refusé", "bloque": " — bloqué", "erreur": " — erreur",
           "commande_directe": " — commande sans code"}


@callback
def async_describe_events(hass: HomeAssistant, async_describe_event: Callable) -> None:
    @callback
    def describe(event: Event) -> dict[str, str]:
        d = event.data
        detail = f" ({d['detail']})" if d.get("detail") else ""
        return {
            LOGBOOK_ENTRY_NAME: "Codes d'accès",
            LOGBOOK_ENTRY_MESSAGE: f"{d.get('action', '')} par {d.get('qui', '?')}{_RESULT.get(d.get('resultat'), '')}"
                                   f"{detail}",
            LOGBOOK_ENTRY_ENTITY_ID: d.get("entity_id"),
        }

    async_describe_event(DOMAIN, EVENT_ACTION, describe)
