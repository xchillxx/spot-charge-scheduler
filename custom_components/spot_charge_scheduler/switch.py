"""The manual master switch: only while ON does this integration actuate
the configured charge switch at all — see coordinator._actuate_switch's
hands-off-when-off policy. Deliberately manual only (no auto-detection
against surplus-load-switch) — confirmed with the user: the PV wallbox's
own surplus mode has to be switched off by hand outside HA anyway, so
there's nothing to auto-detect against."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SpotChargeCoordinator
from .device import hub_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpotChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MasterSwitch(coordinator, entry)])


class MasterSwitch(CoordinatorEntity[SpotChargeCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Lademodus aktiv"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_master_switch"

    @property
    def device_info(self):
        return hub_device_info(self._entry.entry_id)

    @property
    def is_on(self) -> bool:
        return self.coordinator.planner_state.master_switch_on

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_master_switch(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_master_switch(False)
