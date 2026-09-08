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

from .const import DOMAIN, OPPORTUNISTIC_LOOKBACK_DAYS
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
        CheapThresholdSensor(coordinator, entry),
        FuelPriceSensor(coordinator, entry),
        CombustionBreakEvenSensor(coordinator, entry),
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
        cur = self.coordinator.data.get("current_soc")
        tgt = self.coordinator.data.get("target_soc")
        if cur is not None and tgt is not None and cur >= tgt:
            # Guaranteed target met. If a car charge-limit is configured and
            # there's still cheap headroom being planned, we're topping up
            # opportunistically rather than fully done.
            ceiling = getattr(plan, "effective_ceiling_soc", None) or tgt
            if cur < ceiling and plan.slots:
                return "opportunistisch"
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
            "aktiver_zyklus": active.name if active else None,
            "naechste_slots": [
                {"start": s.start.isoformat(), "preis_eur_kwh": s.price} for s in plan.slots
            ],
            "geschaetzte_kosten_eur": plan.estimated_cost_eur,
            "geschaetzte_fertigstellung": (
                plan.estimated_completion.isoformat() if plan.estimated_completion else None
            ),
            "benoetigte_slots": plan.required_slot_count,
            "verfuegbare_slots": plan.available_slot_count,
            "opportunistische_slots": plan.opportunistic_slot_count,
            "effektives_ladelimit_soc": plan.effective_ceiling_soc,
            "auto_ladelimit_soc": self.coordinator.data.get("car_charge_limit"),
            "billig_schwelle_eur_kwh": (
                round(t, 4)
                if (t := self.coordinator.data.get("cheap_price_threshold")) is not None
                else None
            ),
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

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}
        active = self.coordinator.data.get("active_occurrence")
        if not active:
            return {}
        return {"ziel_soc": active.target_soc, "titel": active.name}


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


class CheapThresholdSensor(_BaseSensor):
    """What the "Billig-Schwelle (Perzentil)" number currently works out to
    in ct/kWh, against the last OPPORTUNISTIC_LOOKBACK_DAYS days of observed
    prices — so the slider's abstract percentile has a concrete price next
    to it. Unknown until the price archive has enough history to trust the
    percentile (see price_baseline.cheap_price_threshold)."""

    _attr_name = "Billig-Schwelle"
    _attr_icon = "mdi:cash-clock"
    _attr_native_unit_of_measurement = "ct/kWh"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cheap_threshold"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        eur_kwh = data.get("cheap_price_threshold")
        return round(eur_kwh * 100, 2) if eur_kwh is not None else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "perzentil": self.coordinator.planner_state.opportunistic_percentile,
            "zeitraum_tage": OPPORTUNISTIC_LOOKBACK_DAYS,
            "auto_ladelimit_soc": data.get("car_charge_limit"),
            "opportunistisch_aktiv": (
                data.get("cheap_price_threshold") is not None
                and data.get("car_charge_limit") is not None
            ),
        }


class FuelPriceSensor(_BaseSensor):
    """Fuel price used for the comparison: the cheapest local Tankerkönig
    price when available, otherwise the manually set fallback (the `quelle`
    attribute says which)."""

    _attr_name = "Spritpreis"
    _attr_icon = "mdi:gas-station"
    _attr_native_unit_of_measurement = "€/L"
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_fuel_price"

    @property
    def native_value(self) -> float | None:
        c = (self.coordinator.data or {}).get("combustion")
        return c.get("fuel_price_eur_l") if c else None

    @property
    def extra_state_attributes(self):
        c = (self.coordinator.data or {}).get("combustion")
        if not c:
            return {}
        return {
            "quelle": c.get("fuel_price_source"),
            "kraftstoffart": c.get("fuel_type"),
            "tankstelle": c.get("station"),
            "entfernung_km": c.get("station_distance_km"),
        }


class CombustionBreakEvenSensor(_BaseSensor):
    """Break-even electricity price: at/above this many ct/kWh, driving the
    combustion car costs the same per kilometre as charging. Below it, the
    EV is cheaper. Works off the live fuel price when available, otherwise
    the manually set fallback."""

    _attr_name = "Verbrenner-Break-even"
    _attr_icon = "mdi:scale-balance"
    _attr_native_unit_of_measurement = "ct/kWh"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_combustion_break_even"

    @property
    def native_value(self) -> float | None:
        c = (self.coordinator.data or {}).get("combustion")
        if not c or c.get("break_even_eur_kwh") is None:
            return None
        return round(c["break_even_eur_kwh"] * 100, 1)

    @property
    def extra_state_attributes(self):
        c = (self.coordinator.data or {}).get("combustion")
        if not c:
            return {}
        cur = c.get("current_price_eur_kwh")
        return {
            "guenstiger_jetzt": c.get("cheaper_now"),
            "aktueller_strompreis_ct_kwh": round(cur * 100, 2) if cur is not None else None,
            "verbrenner_ct_100km": round(c["ice_eur_100km"] * 100, 1) if c.get("ice_eur_100km") is not None else None,
            "eauto_ct_100km_jetzt": round(c["ev_eur_100km_now"] * 100, 1) if c.get("ev_eur_100km_now") is not None else None,
            "verbrenner_verbrauch_l_100km": c.get("ice_l_100km"),
            "eauto_verbrauch_kwh_100km": c.get("ev_kwh_100km"),
            "spritpreis_eur_l": c.get("fuel_price_eur_l"),
            "spritpreis_quelle": c.get("fuel_price_source"),
            "kraftstoffart": c.get("fuel_type"),
        }
