"""Diagnostic/status sensors — the plan itself is control-relevant data the
coordinator already computes every cycle, these just expose it for the
dashboard (section 5's transparency requirement)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SpotChargeCoordinator
from .device import hub_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpotChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ChargePlanSensor(coordinator, entry),
        NextCycleSensor(coordinator, entry),
        CalibratedCapacitySensor(coordinator, entry),
        CalibratedChargePowerSensor(coordinator, entry),
    ])


class _BaseSensor(CoordinatorEntity[SpotChargeCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self):
        return hub_device_info(self._entry.entry_id)


class ChargePlanSensor(_BaseSensor):
    _attr_name = "Ladeplan"
    _attr_icon = "mdi:calendar-clock-outline"

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_plan"

    @property
    def native_value(self) -> str:
        plan = self.coordinator.data.get("plan") if self.coordinator.data else None
        if plan is None or self.coordinator.data.get("target_datetime") is None:
            return "kein_ziel"
        if plan.target_reachable is None:
            return "kein_ziel"
        if self.coordinator.data.get("current_soc") is not None and self.coordinator.data[
            "current_soc"
        ] >= self.coordinator.data["target_soc"]:
            return "ziel_erreicht"
        if self.coordinator.data.get("is_home") is False:
            return "nicht_zuhause"
        if self.coordinator.data.get("defer_for_data"):
            return "wartet_auf_daten"
        return "erreichbar" if plan.target_reachable else "nicht_erreichbar"

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        plan = self.coordinator.data.get("plan")
        if plan is None:
            return {}
        active = self.coordinator.data.get("active_occurrence")
        return {
            "aktiver_zyklus": active.summary if active else None,
            "naechste_slots": [
                {"start": s.start.isoformat(), "preis_eur_kwh": s.price} for s in plan.slots
            ],
            "geschaetzte_kosten_eur": plan.estimated_cost_eur,
            "geschaetzte_fertigstellung": (
                plan.estimated_completion.isoformat() if plan.estimated_completion else None
            ),
            "benoetigte_slots": plan.required_slot_count,
            "verfuegbare_slots": plan.available_slot_count,
        }


class NextCycleSensor(_BaseSensor):
    _attr_name = "Nächster Zyklus"
    _attr_device_class = "timestamp"
    _attr_icon = "mdi:calendar-refresh"

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_next_cycle"

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        target_dt = self.coordinator.data.get("target_datetime")
        return dt_util.as_utc(target_dt) if target_dt else None


class CalibratedCapacitySensor(_BaseSensor):
    _attr_name = "Kalibrierte Kapazität"
    _attr_icon = "mdi:battery-sync"
    _attr_native_unit_of_measurement = "kWh"

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_calibrated_capacity"

    @property
    def native_value(self) -> float:
        return self.coordinator.planner_state.battery_capacity_kwh

    @property
    def extra_state_attributes(self):
        return {"anzahl_ladevorgaenge": len(self.coordinator.planner_state.capacity_samples)}


class CalibratedChargePowerSensor(_BaseSensor):
    _attr_name = "Kalibrierte Ladeleistung"
    _attr_icon = "mdi:ev-station"
    _attr_native_unit_of_measurement = "kW"

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_calibrated_charge_power"

    @property
    def native_value(self) -> float:
        return self.coordinator.planner_state.charge_power_kw

    @property
    def extra_state_attributes(self):
        return {"anzahl_ladevorgaenge": len(self.coordinator.planner_state.power_samples)}
