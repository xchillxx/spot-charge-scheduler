"""Spot Charge Scheduler — Home Assistant custom integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .coordinator import SpotChargeCoordinator

ADD_CYCLE_SCHEMA = vol.Schema({
    vol.Required("start"): cv.datetime,
    vol.Required("target_soc"): vol.Coerce(float),
    vol.Optional("rhythm_days", default=0): vol.Coerce(int),
    vol.Optional("summary", default=""): cv.string,
})

UPDATE_CYCLE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Optional("target_soc"): vol.Coerce(float),
    vol.Optional("rhythm_days"): vol.Coerce(int),
})

# Matches the unique_id format switch.py gives every per-cycle pause switch
# (f"{entry_id}_cycle_paused_{cycle_id}") — used as the "pick which cycle"
# handle for update_cycle, so the form gets a proper searchable entity
# picker instead of asking the user to type an opaque cycle id.
_CYCLE_SWITCH_MARKER = "_cycle_paused_"


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
    # Domain-global, not per-entry — each service is only (re-)registered
    # once regardless of how many times a config entry is set up/reloaded.
    # Checked per-service, not as a single guard for the whole function —
    # an early return here on just the first service's presence would
    # silently skip registering any service added in a later version for
    # everyone who already had an earlier version's services registered
    # this HA session (a real bug caught while adding update_cycle after
    # add_cycle already shipped). Fine to assume a single coordinator: this
    # integration manages one vehicle per HA instance in practice, same
    # assumption the dashboard/services.yaml UI makes.

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

    if not hass.services.has_service(DOMAIN, "add_cycle"):
        hass.services.async_register(DOMAIN, "add_cycle", _async_handle_add_cycle, schema=ADD_CYCLE_SCHEMA)

    async def _async_handle_update_cycle(call: ServiceCall) -> None:
        coordinators = list(hass.data.get(DOMAIN, {}).values())
        if not coordinators:
            raise HomeAssistantError("Spot Charge Scheduler ist nicht eingerichtet.")

        registry = er.async_get(hass)
        entity_entry = registry.async_get(call.data["entity_id"])
        unique_id = entity_entry.unique_id if entity_entry else None
        if not unique_id or _CYCLE_SWITCH_MARKER not in unique_id:
            raise HomeAssistantError(
                "Das ist keine Zyklus-Entität von Spot Charge Scheduler — bitte den "
                "'Pausiert: ...'-Schalter des gewünschten Zyklus auswählen."
            )
        cycle_id = unique_id.split(_CYCLE_SWITCH_MARKER, 1)[1]

        target_soc = call.data.get("target_soc")
        rhythm_days = call.data.get("rhythm_days")
        if target_soc is None and rhythm_days is None:
            raise HomeAssistantError("Ziel-SoC und/oder Wiederhol-Rhythmus angeben — sonst gibt es nichts zu ändern.")

        await coordinators[0].async_update_cycle(cycle_id, target_soc, rhythm_days)

    if not hass.services.has_service(DOMAIN, "update_cycle"):
        hass.services.async_register(
            DOMAIN, "update_cycle", _async_handle_update_cycle, schema=UPDATE_CYCLE_SCHEMA
        )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: SpotChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_flush_state()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
