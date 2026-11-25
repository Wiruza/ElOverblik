"""Config flow for the Eloverblik integration.

Fork of homeassistant-eloverblik by JonasPed (Apache-2.0), changed for Power Monitor:
instead of typing the 18-digit meter number, you paste your key and pick the meter from
the list Eloverblik returns. A re-authentication step asks for a new key when the old
one expires (keys last up to a year).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import EloverblikAuthError, EloverblikClient, EloverblikRateLimitError, Meter
from .const import (
    CONF_ADDRESS,
    CONF_HISTORY_DAYS,
    CONF_KIND,
    CONF_METERING_POINT,
    CONF_REFRESH_TOKEN,
    DEFAULT_HISTORY_DAYS,
    DOMAIN,
    HISTORY_CHOICES,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_SCHEMA = vol.Schema(
    {vol.Required(CONF_REFRESH_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


class EloverblikConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eloverblik."""

    VERSION = 1

    def __init__(self) -> None:
        self._token: str = ""
        self._meters: list[Meter] = []

    async def _async_fetch_meters(self, token: str) -> tuple[list[Meter], str | None]:
        client = EloverblikClient(async_get_clientsession(self.hass), token)
        try:
            return await client.async_get_metering_points(), None
        except EloverblikAuthError:
            return [], "invalid_auth"
        except EloverblikRateLimitError:
            return [], "rate_limited"
        except Exception:  # noqa: BLE001 - shown to the person as "cannot connect"
            _LOGGER.exception("Could not read the meters from Eloverblik")
            return [], "cannot_connect"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the Eloverblik key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_REFRESH_TOKEN].strip()
            meters, error = await self._async_fetch_meters(token)
            if error:
                errors["base"] = error
            else:
                configured = {e.unique_id for e in self._async_current_entries()}
                linked = [m for m in meters if m.has_relation]
                available = [m for m in linked if m.id not in configured]
                if not linked:
                    errors["base"] = "no_meters"
                elif not available:
                    return self.async_abort(reason="already_configured")
                else:
                    self._token = token
                    self._meters = available
                    return await self.async_step_meter()
        return self.async_show_form(step_id="user", data_schema=TOKEN_SCHEMA, errors=errors)

    async def async_step_meter(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the metering point and how much history to fetch."""
        if user_input is not None:
            meter = next(m for m in self._meters if m.id == user_input[CONF_METERING_POINT])
            await self.async_set_unique_id(meter.id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=meter.address,
                data={
                    CONF_REFRESH_TOKEN: self._token,
                    CONF_METERING_POINT: meter.id,
                    CONF_ADDRESS: meter.address,
                    CONF_KIND: meter.kind,
                    CONF_HISTORY_DAYS: int(user_input[CONF_HISTORY_DAYS]),
                },
            )
        options = [
            SelectOptionDict(value=m.id, label=f"{m.address} – {m.kind_label} (…{m.short_id})")
            for m in self._meters
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_METERING_POINT, default=self._meters[0].id): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                ),
                vol.Required(CONF_HISTORY_DAYS, default=str(DEFAULT_HISTORY_DAYS)): SelectSelector(
                    SelectSelectorConfig(
                        options=HISTORY_CHOICES,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="history_days",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="meter", data_schema=schema)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The key stopped working (expired or switched off)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_REFRESH_TOKEN].strip()
            meters, error = await self._async_fetch_meters(token)
            entry = self._get_reauth_entry()
            if error:
                errors["base"] = error
            elif not any(m.id == entry.unique_id for m in meters):
                errors["base"] = "wrong_meter"
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_REFRESH_TOKEN: token})
        return self.async_show_form(step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors)
