"""Number entities: the live, freely-editable planning values. These read
and write coordinator.planner_state directly (see coordinator.py's setters)
rather than the config entry — editing them must not reload the integration
mid-session."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    async_add_entities([BatteryCapacityNumber(coordinator, entry), ChargePowerNumber(coordinator, entry)])


class _BaseNumber(CoordinatorEntity[SpotChargeCoordinator], NumberEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self):
        return hub_device_info(self._entry.entry_id)


class BatteryCapacityNumber(_BaseNumber):
    """Used for plan sizing; overwritten automatically once the self-
    calibrating estimator (see capacity_estimator.py) has enough real
    charge sessions to trust — see sensor.py's diagnostic counterpart for
    how many samples that estimate is currently based on."""

    _attr_name = "Akkukapazität"
    _attr_icon = "mdi:battery-high"
    _attr_native_min_value = 1
    _attr_native_max_value = 200
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kWh"
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_battery_capacity"

    @property
    def native_value(self) -> float:
        return self.coordinator.planner_state.battery_capacity_kwh

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_battery_capacity_kwh(value)


class ChargePowerNumber(_BaseNumber):
    """Used for plan sizing (how many slots are needed); overwritten
    automatically once the self-calibrating estimator has enough real
    charge sessions to trust, same as BatteryCapacityNumber — only kicks in
    when a charge-power sensor is configured, otherwise stays at the
    config default forever."""

    _attr_name = "Ladeleistung"
    _attr_icon = "mdi:ev-station"
    _attr_native_min_value = 0.1
    _attr_native_max_value = 50
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "kW"
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charge_power"

    @property
    def native_value(self) -> float:
        return self.coordinator.planner_state.charge_power_kw

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_charge_power_kw(value)
