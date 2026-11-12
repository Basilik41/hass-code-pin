"""Installation en un clic ; réglages dans les options."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    LabelSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ALARM_CODES,
    CONF_ALIASES,
    CONF_EXEMPT,
    CONF_LABEL,
    CONF_MODE,
    CONF_NOTIFY,
    CONF_PEER_TOKEN,
    CONF_PEER_URL,
    CONF_TABLET,
    DEFAULT_NOTIFY,
    DEFAULT_TABLET,
    DOMAIN,
    MODE_BLOCAGE,
    MODE_OBSERVATION,
)


class CodePinFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Codes d'accès", data={}, options={
                CONF_MODE: MODE_OBSERVATION, CONF_TABLET: DEFAULT_TABLET, CONF_NOTIFY: DEFAULT_NOTIFY})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CodePinOptions()


class CodePinOptions(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        o = dict(self.config_entry.options)
        if user_input is not None:
            url = (user_input.get(CONF_PEER_URL) or "").strip()
            tok = (user_input.get(CONF_PEER_TOKEN) or "").strip()
            if url and not url.startswith(("http://", "https://")):
                errors[CONF_PEER_URL] = "url"
            elif bool(url) != bool(tok):
                errors[CONF_PEER_TOKEN] = "pair_incomplet"
            if not errors:
                return self.async_create_entry(data={**user_input, CONF_PEER_URL: url, CONF_PEER_TOKEN: tok})
            o.update(user_input)
        users = [u for u in await self.hass.auth.async_get_users() if not u.system_generated and u.is_active]
        user_opts = [SelectOptionDict(value=u.id, label=u.name or u.id) for u in users]

        def sv(key: str) -> dict | None:
            v = o.get(key)
            return {"suggested_value": v} if v not in (None, "", []) else None

        schema = vol.Schema({
            vol.Required(CONF_MODE, default=o.get(CONF_MODE, MODE_OBSERVATION)): SelectSelector(SelectSelectorConfig(
                options=[SelectOptionDict(value=MODE_OBSERVATION, label="Observation : signaler seulement"),
                         SelectOptionDict(value=MODE_BLOCAGE, label="Blocage : annuler les commandes sans code")],
                mode=SelectSelectorMode.LIST)),
            vol.Optional(CONF_LABEL, description=sv(CONF_LABEL)): LabelSelector(),
            vol.Required(CONF_TABLET, default=o.get(CONF_TABLET, DEFAULT_TABLET)): TextSelector(),
            vol.Optional(CONF_EXEMPT, default=list(o.get(CONF_EXEMPT) or [])): SelectSelector(SelectSelectorConfig(
                options=user_opts, multiple=True, mode=SelectSelectorMode.DROPDOWN)),
            vol.Optional(CONF_NOTIFY, description=sv(CONF_NOTIFY)): TextSelector(),
            vol.Optional(CONF_ALIASES, description=sv(CONF_ALIASES)): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Optional(CONF_ALARM_CODES, description=sv(CONF_ALARM_CODES)): TextSelector(
                TextSelectorConfig(multiline=True)),
            vol.Optional(CONF_PEER_URL, description=sv(CONF_PEER_URL)): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)),
            vol.Optional(CONF_PEER_TOKEN, description=sv(CONF_PEER_TOKEN)): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
