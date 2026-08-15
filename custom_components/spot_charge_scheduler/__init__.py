"""Spot Charge Scheduler — Home Assistant custom integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import SpotChargeCoordinator

ADD_CYCLE_SCHEMA = vol.Schema({
    vol.Required("start"): cv.datetime,
    vol.Required("target_soc"): vol.Coerce(float),
    vol.Optional("rhythm_days", default=0): vol.Coerce(int),
    vol.Optional("summary", default=""): cv.string,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SpotChargeCoordinator(hass, {**entry.data, **entry.options}, entry.entry_id)
    await coordinator.async_setup()
    await coordinator.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _async_register_services(hass)
    return True


def _async_register_services(hass: HomeAssistant) -> None:
    # Domain-global, not per-entry — only registered once regardless of how
    # many times a config entry is set up/reloaded. Fine to assume a single
    # coordinator: this integration manages one vehicle per HA instance in
    # practice, same assumption the dashboard/services.yaml UI makes.
    if hass.services.has_service(DOMAIN, "add_cycle"):
        return

    async def _async_handle_add_cycle(call: ServiceCall) -> None:
        coordinators = list(hass.data.get(DOMAIN, {}).values())
        if not coordinators:
            raise HomeAssistantError("Spot Charge Scheduler ist nicht eingerichtet.")
        await coordinators[0].async_add_cycle(
            anchor=call.data["start"],
            target_soc=call.data["target_soc"],
            rhythm_days=call.data["rhythm_days"],
            summary=call.data["summary"],
        )

    hass.services.async_register(DOMAIN, "add_cycle", _async_handle_add_cycle, schema=ADD_CYCLE_SCHEMA)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: SpotChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_flush_state()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
