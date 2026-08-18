"""DataUpdateCoordinator: reads vehicle/price state, (re)computes the charge
plan, and actuates the configured charge switch accordingly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import capacity_estimator, price_baseline, readiness, schedule
from .const import (
    CONF_BATTERY_CAPACITY_KWH_DEFAULT,
    CONF_CHARGE_POWER_KW,
    CONF_CHARGE_POWER_SENSOR,
    CONF_CHARGE_SWITCH,
    CONF_CHARGING_STATUS_SENSOR,
    CONF_ENERGY_ADDED_SENSOR,
    CONF_HOME_ZONE_ENTITY,
    CONF_LOCATION_TRACKER_ENTITY,
    CONF_PLUGGED_IN_SENSOR,
    CONF_PRICE_SOURCE,
    CONF_SOC_SENSOR,
    CONF_TIBBER_HOME_NICKNAME,
    DOMAIN,
    PRICE_FETCH_MIN_INTERVAL_SECONDS,
    UPDATE_INTERVAL_SECONDS,
)
from .planner import ChargePlan, compute_plan
from .planner_state import PlannerState
from .price_source import SLOT_DURATION, PricePoint, get_price_provider
from .schedule import Occurrence

_LOGGER = logging.getLogger(__name__)


def _get_float_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def _get_bool_state(hass: HomeAssistant, entity_id: str | None) -> bool | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    return state.state == "on"


def _get_is_home(hass: HomeAssistant, tracker_entity: str | None, zone_entity: str | None) -> bool | None:
    """None when location gating isn't configured (both fields optional and
    only meaningful together) — callers must not gate on that as False."""
    if not tracker_entity or not zone_entity:
        return None
    tracker_state = hass.states.get(tracker_entity)
    if tracker_state is None or tracker_state.state in ("unknown", "unavailable"):
        return None
    # A device_tracker's state is the object_id of whichever zone it's
    # currently inside (e.g. "home" for zone.home), or "not_home" — the
    # standard HA zone-matching convention, not something this integration
    # computes itself.
    zone_object_id = zone_entity.split(".", 1)[1]
    return tracker_state.state == zone_object_id


class SpotChargeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config: dict, entry_id: str) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS)
        )
        self._config = config
        self.planner_state = PlannerState(
            hass,
            entry_id,
            default_capacity_kwh=config[CONF_BATTERY_CAPACITY_KWH_DEFAULT],
            default_charge_power_kw=config[CONF_CHARGE_POWER_KW],
        )
        self._price_provider = get_price_provider(
            config[CONF_PRICE_SOURCE], config[CONF_TIBBER_HOME_NICKNAME]
        )
        self._cached_prices: list[PricePoint] = []
        self._last_price_fetch: datetime | None = None
        self._was_charging: bool | None = None
        # Tracks the active occurrence's start so a change is detected even
        # when nobody edited anything — e.g. the previous target got
        # abandoned (MISSED_DEADLINE_GRACE_HOURS) or simply completed and
        # the next one in line took over. Without this, the cache from the
        # old target's fetch just sits there not covering the new one, and
        # the 15-min fetch-spacing throttle (meant to avoid hammering the
        # service while genuinely waiting on the SAME target) ends up
        # delaying the fetch this actually-new target needs right now.
        self._last_active_target_dt: datetime | None = None

    async def async_setup(self) -> None:
        await self.planner_state.async_load()

    async def async_flush_state(self) -> None:
        await self.planner_state.async_save_now()

    # --- entity/calendar-facing setters (bypass the config-entry reload path) ---

    async def async_add_cycle(
        self, anchor: datetime, target_soc: float, rhythm_days: int, summary: str
    ) -> str:
        """Create a new charge-target cycle (recurring if rhythm_days > 0,
        one-off otherwise — e.g. an ad-hoc "I need to leave early" addition).
        Returns the new cycle's id."""
        cycle_id = schedule.new_cycle_id()
        self.planner_state.cycles.append({
            "id": cycle_id,
            "summary": summary,
            "target_soc": target_soc,
            "anchor": dt_util.as_local(anchor).isoformat(),
            "rhythm_days": rhythm_days,
            "enabled": True,
        })
        self._invalidate_price_cache()
        self.planner_state.async_save()
        await self.async_request_refresh()
        return cycle_id

    async def async_set_cycle_enabled(self, cycle_id: str, enabled: bool) -> None:
        """Pause/resume an entire recurring series (e.g. for vacation) —
        unlike deleting an occurrence, this doesn't touch any individual
        instance, so resuming brings back every occurrence exactly as
        scheduled, including ones that would have fired while paused."""
        for cycle in self.planner_state.cycles:
            if cycle["id"] == cycle_id:
                cycle["enabled"] = enabled
                break
        self._invalidate_price_cache()
        self.planner_state.async_save()
        await self.async_request_refresh()

    async def async_update_cycle(
        self, cycle_id: str, target_soc: float | None, rhythm_days: int | None
    ) -> None:
        """Change a cycle's target SoC and/or rhythm for every FUTURE
        occurrence at once (e.g. "80% is fine now, but 50% is enough once
        winter prices get expensive") — unlike an occurrence override,
        which only ever affects a single dragged/deleted instance, this
        edits the series itself. Occurrences already individually
        rescheduled (occurrence_overrides) keep their overridden start
        time; only the target_soc/rhythm they'd otherwise inherit changes."""
        for cycle in self.planner_state.cycles:
            if cycle["id"] == cycle_id:
                if target_soc is not None:
                    cycle["target_soc"] = target_soc
                if rhythm_days is not None:
                    cycle["rhythm_days"] = rhythm_days
                break
        self._invalidate_price_cache()
        self.planner_state.async_save()
        await self.async_request_refresh()

    async def async_set_occurrence_start(
        self, cycle_id: str, original_start: datetime, new_start: datetime
    ) -> None:
        """Reschedule ONE occurrence of a (possibly recurring) cycle — the
        rest of the series is untouched, same as dragging a single instance
        in any calendar app."""
        key = schedule.override_key(cycle_id, original_start)
        override = self.planner_state.occurrence_overrides.setdefault(key, {})
        override["start"] = dt_util.as_local(new_start).isoformat()
        override.pop("deleted", None)
        self._invalidate_price_cache()
        self.planner_state.async_save()
        await self.async_request_refresh()

    async def async_delete_occurrence(self, cycle_id: str, original_start: datetime) -> None:
        key = schedule.override_key(cycle_id, original_start)
        self.planner_state.occurrence_overrides[key] = {"deleted": True}
        self._invalidate_price_cache()
        self.planner_state.async_save()
        await self.async_request_refresh()

    def _invalidate_price_cache(self) -> None:
        # Any change to the schedule can change which deadline is active,
        # which changes the price window that needs fetching.
        self._cached_prices = []
        self._last_price_fetch = None

    async def async_set_battery_capacity_kwh(self, value: float) -> None:
        self.planner_state.battery_capacity_kwh = value
        self.planner_state.async_save()
        await self.async_request_refresh()

    async def async_set_charge_power_kw(self, value: float) -> None:
        self.planner_state.charge_power_kw = value
        self.planner_state.async_save()
        await self.async_request_refresh()

    async def async_set_master_switch(self, value: bool) -> None:
        self.planner_state.master_switch_on = value
        self.planner_state.async_save()
        if not value:
            # A one-time action tied to the explicit off transition, not
            # ongoing per-cycle enforcement (see _actuate_switch's hands-off
            # policy while master is off) — otherwise turning "Lademodus
            # aktiv" off would silently leave an already-running managed
            # charge session going until it happened to hit a stop
            # condition on its own.
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._config[CONF_CHARGE_SWITCH]}, blocking=True
            )
        await self.async_request_refresh()

    # --- main cycle ---

    async def _async_update_data(self) -> dict:
        now = dt_util.now()

        current_soc = _get_float_state(self.hass, self._config[CONF_SOC_SENSOR])
        is_charging = _get_bool_state(self.hass, self._config.get(CONF_CHARGING_STATUS_SENSOR))
        plugged_in = _get_bool_state(self.hass, self._config.get(CONF_PLUGGED_IN_SENSOR))
        energy_added = _get_float_state(self.hass, self._config.get(CONF_ENERGY_ADDED_SENSOR))
        current_power_kw = _get_float_state(self.hass, self._config.get(CONF_CHARGE_POWER_SENSOR))
        is_home = _get_is_home(
            self.hass, self._config.get(CONF_LOCATION_TRACKER_ENTITY), self._config.get(CONF_HOME_ZONE_ENTITY)
        )

        if is_charging is not None:
            capacity_estimator.process_charging_edge(
                self.planner_state, self._was_charging, is_charging, current_soc, energy_added, current_power_kw
            )
            self._was_charging = is_charging

        # Which occurrence (across all cycles) we're planning/charging
        # toward right now — see schedule.find_active_occurrence for how
        # several independent recurring cycles interleave without any
        # explicit "advance to next" step; it's always freshly derived.
        active: Occurrence | None = schedule.find_active_occurrence(
            self.planner_state.cycles, self.planner_state.occurrence_overrides, now, current_soc
        )
        target_dt = active.start if active else None
        target_soc = active.target_soc if active else None

        if target_dt != self._last_active_target_dt:
            self._invalidate_price_cache()
            self._last_active_target_dt = target_dt

        if target_dt is not None:
            await self._maybe_fetch_prices(now, target_dt)

        plan = self._compute_plan(now, target_dt, target_soc, current_soc)
        self.planner_state.plan = _plan_to_dict(plan)

        defer_for_data = self._should_defer_for_data(now, target_dt, plan)

        await self._actuate_switch(
            plan, current_soc, target_soc, plugged_in, is_home, defer_for_data, now, target_dt
        )

        return {
            "current_soc": current_soc,
            "is_charging": is_charging,
            "plugged_in": plugged_in,
            "is_home": is_home,
            "active_occurrence": active,
            "target_soc": target_soc,
            "target_datetime": target_dt,
            "defer_for_data": defer_for_data,
            "battery_capacity_kwh": self.planner_state.battery_capacity_kwh,
            "capacity_sample_count": len(self.planner_state.capacity_samples),
            "charge_power_kw": self.planner_state.charge_power_kw,
            "power_sample_count": len(self.planner_state.power_samples),
            "master_switch_on": self.planner_state.master_switch_on,
            "plan": plan,
        }

    async def _maybe_fetch_prices(self, now: datetime, target_dt: datetime) -> None:
        cache_covers_target = bool(self._cached_prices) and (
            self._cached_prices[-1].start + SLOT_DURATION >= target_dt
        )
        if cache_covers_target:
            return
        if self._last_price_fetch is not None and (
            now - self._last_price_fetch
        ).total_seconds() < PRICE_FETCH_MIN_INTERVAL_SECONDS:
            return
        try:
            self._cached_prices = await self._price_provider.async_get_prices(self.hass, now, target_dt)
            self.planner_state.price_history = price_baseline.merge_observations(
                self.planner_state.price_history, self._cached_prices, now
            )
            self.planner_state.async_save()
        except Exception:  # noqa: BLE001 - a failed fetch must not crash the cycle
            _LOGGER.exception("Failed to fetch prices")
        finally:
            self._last_price_fetch = now

    def _should_defer_for_data(
        self, now: datetime, target_dt: datetime | None, plan: ChargePlan
    ) -> bool:
        """See readiness.py. Only meaningful with an actual target and a
        real plan (plan.required_slot_count/target_reachable are None when
        there's no active occurrence at all)."""
        if target_dt is None or plan.target_reachable is None or plan.required_slot_count == 0:
            return False
        data_covers_target = bool(self._cached_prices) and (
            self._cached_prices[-1].start + SLOT_DURATION >= target_dt
        )
        eligible_prices = [p.price for p in self._cached_prices if now <= p.start < target_dt]
        best_known_price = min(eligible_prices) if eligible_prices else None
        historical_typical_price = price_baseline.typical_price_for_time_of_day(
            self.planner_state.price_history, now
        )
        return readiness.should_defer_for_better_data(
            now, target_dt, plan.required_slot_count, data_covers_target,
            best_known_price, historical_typical_price,
        )

    def _compute_plan(
        self,
        now: datetime,
        target_dt: datetime | None,
        target_soc: float | None,
        current_soc: float | None,
    ) -> ChargePlan:
        if target_dt is None or target_soc is None or current_soc is None:
            return ChargePlan(
                slots=[], estimated_cost_eur=0.0, estimated_completion=None,
                target_reachable=None, required_slot_count=0, available_slot_count=0,
            )
        return compute_plan(
            now=now,
            target_datetime=target_dt,
            target_soc=target_soc,
            current_soc=current_soc,
            battery_capacity_kwh=self.planner_state.battery_capacity_kwh,
            charge_power_kw=self.planner_state.charge_power_kw,
            price_points=self._cached_prices,
        )

    async def _actuate_switch(
        self,
        plan: ChargePlan,
        current_soc: float | None,
        target_soc: float | None,
        plugged_in: bool | None,
        is_home: bool | None,
        defer_for_data: bool,
        now: datetime,
        target_dt: datetime | None,
    ) -> None:
        if not self.planner_state.master_switch_on:
            return  # hands-off: don't touch the charge switch at all

        desired_on = self._decide_desired_state(
            plan, current_soc, target_soc, plugged_in, is_home, defer_for_data, now, target_dt
        )

        switch_entity = self._config[CONF_CHARGE_SWITCH]
        current_state = self.hass.states.get(switch_entity)
        currently_on = current_state is not None and current_state.state == "on"
        if desired_on == currently_on:
            return

        await self.hass.services.async_call(
            "switch",
            "turn_on" if desired_on else "turn_off",
            {"entity_id": switch_entity},
            blocking=True,
        )

    def _decide_desired_state(
        self,
        plan: ChargePlan,
        current_soc: float | None,
        target_soc: float | None,
        plugged_in: bool | None,
        is_home: bool | None,
        defer_for_data: bool,
        now: datetime,
        target_dt: datetime | None,
    ) -> bool:
        if target_soc is None:
            return False  # no active occurrence at all — nothing to charge toward
        if current_soc is not None and current_soc >= target_soc:
            return False
        if defer_for_data:
            # Price data doesn't cover the full window yet, and there's
            # provably enough slack to wait for it without risking the
            # deadline — see readiness.py. Once that stops being true
            # (deadline gets close, or fuller/better-looking data arrives),
            # this flips back to False on its own next cycle.
            return False
        if plugged_in is False:
            return False
        if is_home is False:
            # Location gating configured and the vehicle isn't in the
            # chosen zone right now — never mind the schedule, there's
            # nothing to charge here. Checked even in the past-deadline
            # fallback below, since forcing a switch on remotely with
            # nothing connected accomplishes nothing.
            return False
        if target_dt is not None and now >= target_dt:
            # Deadline already blown and target not met: best-effort charge
            # regardless of the (now stale/empty) plan. Section 2.5's "target
            # beats cost" fallback taken to its logical extreme.
            return True
        return any(s.start <= now < s.start + SLOT_DURATION for s in plan.slots)


def _plan_to_dict(plan: ChargePlan) -> dict:
    return {
        "slots": [{"start": s.start.isoformat(), "price": s.price} for s in plan.slots],
        "estimated_cost_eur": plan.estimated_cost_eur,
        "estimated_completion": plan.estimated_completion.isoformat() if plan.estimated_completion else None,
        "target_reachable": plan.target_reachable,
        "required_slot_count": plan.required_slot_count,
        "available_slot_count": plan.available_slot_count,
    }
