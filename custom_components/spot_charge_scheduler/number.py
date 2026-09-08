"""Number entities: the live, freely-editable planning values. These read
and write coordinator.planner_state directly (see coordinator.py's setters)
rather than the config entry — editing them must not reload the integration
mid-session."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    entities = [
        BatteryCapacityNumber(coordinator, entry),
        ChargePowerNumber(coordinator, entry),
        OpportunisticPercentileNumber(coordinator, entry),
        IceConsumptionNumber(coordinator, entry),
        EvConsumptionNumber(coordinator, entry),
    ]
    for n in range(1, NUM_CYCLE_SLOTS + 1):
        entities.append(CycleSlotTargetSocNumber(coordinator, entry, n))
        entities.append(CycleSlotRhythmNumber(coordinator, entry, n))
    async_add_entities(entities)


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


class OpportunisticPercentileNumber(_BaseNumber):
    """How cheap a slot has to be, relative to the last few days of observed
    prices, before the planner grabs it as opportunistic top-up beyond the
    guaranteed target SoC (up to the configured car charge-limit entity).
    A slot counts as cheap at/below this percentile of that window. Lower =
    stricter (only the very cheapest slots top up); higher = more eager.
    Has no effect unless a car charge-limit entity is configured, and only
    once the price archive has ~2 days of history. The companion
    "Billig-Schwelle" sensor shows what this percentile currently works out
    to in ct/kWh."""

    _attr_name = "Billig-Schwelle (Perzentil)"
    _attr_icon = "mdi:sale"
    _attr_native_min_value = 1
    _attr_native_max_value = 50
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_opportunistic_percentile"

    @property
    def native_value(self) -> float:
        return self.coordinator.planner_state.opportunistic_percentile

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_opportunistic_percentile(value)


class IceConsumptionNumber(_BaseNumber):
    """Combustion car's fuel use, L/100 km — one half of the break-even
    comparison (see the "Verbrenner-Break-even" sensor)."""

    _attr_name = "Verbrenner-Verbrauch"
    _attr_icon = "mdi:gas-station"
    _attr_native_min_value = 1
    _attr_native_max_value = 30
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "L/100 km"
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ice_consumption"

    @property
    def native_value(self) -> float:
        return self.coordinator.planner_state.ice_consumption_l_100km

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_ice_consumption_l_100km(value)


class EvConsumptionNumber(_BaseNumber):
    """EV's energy use *from the socket*, kWh/100 km (incl. charging losses)
    — the other half of the break-even comparison. Auto-overwritten from
    Home Assistant's own odometer + charge-energy statistics once an
    odometer and a charge-energy entity are configured and there's enough
    distance history; stays at the value set here otherwise."""

    _attr_name = "E-Auto-Verbrauch (ab Steckdose)"
    _attr_icon = "mdi:ev-station"
    _attr_native_min_value = 5
    _attr_native_max_value = 60
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "kWh/100 km"
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ev_consumption"

    @property
    def native_value(self) -> float:
        return self.coordinator.planner_state.ev_consumption_kwh_100km

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_ev_consumption_kwh_100km(value)


class _SlotNumber(_BaseNumber):
    """Shared base for the per-slot number entities — see planner_state.py's
    slot model and coordinator.async_set_slot_field."""

    _attr_entity_category = EntityCategory.CONFIG
    _slot_field: str

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry, slot_no: int) -> None:
        super().__init__(coordinator, entry)
        self._slot_no = slot_no

    @property
    def native_value(self) -> float:
        return float(self.coordinator.get_slot(self._slot_no)[self._slot_field])

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_slot_field(self._slot_no, self._slot_field, value)


class CycleSlotTargetSocNumber(_SlotNumber):
    _slot_field = "target_soc"
    _attr_icon = "mdi:battery-charging-medium"
    _attr_native_min_value = 5
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry, slot_no: int) -> None:
        super().__init__(coordinator, entry, slot_no)
        self._attr_unique_id = f"{entry.entry_id}_slot_{slot_no}_target_soc"
        self._attr_name = f"Slot {slot_no} Ziel-SoC"


class CycleSlotRhythmNumber(_SlotNumber):
    _slot_field = "rhythm_days"
    _attr_icon = "mdi:calendar-sync"
    _attr_native_min_value = 0
    _attr_native_max_value = 30
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "Tage"
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry, slot_no: int) -> None:
        super().__init__(coordinator, entry, slot_no)
        self._attr_unique_id = f"{entry.entry_id}_slot_{slot_no}_rhythm"
        self._attr_name = f"Slot {slot_no} Rhythmus (Tage)"

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_slot_field(self._slot_no, self._slot_field, int(value))
