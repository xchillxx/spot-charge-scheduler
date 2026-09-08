"""Text entities: the free-form name of each cycle slot (e.g. "Tagschicht").
Edited directly on the dashboard; writes straight into the slot dict via
coordinator.async_set_slot_field — see planner_state.py's slot model."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NUM_CYCLE_SLOTS
from .coordinator import SpotChargeCoordinator
from .device import hub_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpotChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CycleSlotNameText(coordinator, entry, n) for n in range(1, NUM_CYCLE_SLOTS + 1)
    )


class CycleSlotNameText(CoordinatorEntity[SpotChargeCoordinator], TextEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:rename-box"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min = 0
    _attr_native_max = 40
    _attr_pattern = None

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry, slot_no: int) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._slot_no = slot_no
        self._attr_unique_id = f"{entry.entry_id}_slot_{slot_no}_name"
        self._attr_name = f"Slot {slot_no} Name"

    @property
    def device_info(self):
        return hub_device_info(self._entry.entry_id)

    @property
    def native_value(self) -> str:
        return str(self.coordinator.get_slot(self._slot_no).get("name") or "")

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_set_slot_field(self._slot_no, "name", value)
