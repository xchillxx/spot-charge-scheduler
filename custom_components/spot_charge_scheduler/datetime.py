"""The active target date/time — state is 'unknown' until the user sets it
the first time, no fixed default (see architecture discussion: no fixed
rhythm start date, the user seeds the first cycle manually)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SpotChargeCoordinator
from .device import hub_device_info

if TYPE_CHECKING:
    # Only for the annotations below, deferred by `from __future__ import
    # annotations` — this module is itself named `datetime`, so a real
    # top-level `from datetime import datetime` risks self-shadowing
    # depending on how the loader resolves sys.path. TYPE_CHECKING keeps
    # the name resolvable for linters/type-checkers without ever executing
    # the import at runtime.
    from datetime import datetime


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpotChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TargetDateTimeEntity(coordinator, entry)])


class TargetDateTimeEntity(CoordinatorEntity[SpotChargeCoordinator], DateTimeEntity):
    _attr_has_entity_name = True
    _attr_name = "Ziel-Zeitpunkt"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_target_datetime"

    @property
    def device_info(self):
        return hub_device_info(self._entry.entry_id)

    @property
    def native_value(self) -> datetime | None:
        raw = self.coordinator.planner_state.target_datetime
        return dt_util.parse_datetime(raw) if raw else None

    async def async_set_value(self, value: datetime) -> None:
        await self.coordinator.async_set_target_datetime(dt_util.as_local(value))
