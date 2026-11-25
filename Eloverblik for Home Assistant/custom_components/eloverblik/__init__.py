"""The Eloverblik integration.

Fork of homeassistant-eloverblik by JonasPed (Apache-2.0), changed for Power Monitor:
setup now creates one coordinator per metering point, kept on the config entry.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import EloverblikCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type EloverblikConfigEntry = ConfigEntry[EloverblikCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EloverblikConfigEntry) -> bool:
    """Set up Eloverblik from a config entry."""
    coordinator = EloverblikCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EloverblikConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
