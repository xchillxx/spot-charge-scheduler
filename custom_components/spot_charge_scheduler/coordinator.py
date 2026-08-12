"""DataUpdateCoordinator: reads vehicle/price state, (re)computes the charge
plan, and actuates the configured charge switch accordingly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import capacity_estimator
from .const import (
    CONF_BATTERY_CAPACITY_KWH_DEFAULT,
    CONF_CHARGE_POWER_KW,
    CONF_CHARGE_SWITCH,
    CONF_CHARGING_STATUS_SENSOR,
    CONF_ENERGY_ADDED_SENSOR,
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


class SpotChargeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config: dict, entry_id: str) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS)
        )
        self._config = config
        self.planner_state = PlannerState(
            hass, entry_id, default_capacity_kwh=config[CONF_BATTERY_CAPACITY_KWH_DEFAULT]
        )
        self._price_provider = get_price_provider(
            config[CONF_PRICE_SOURCE], config[CONF_TIBBER_HOME_NICKNAME]
        )
        self._cached_prices: list[PricePoint] = []
        self._last_price_fetch: datetime | None = None
        self._was_charging: bool | None = None

    async def async_setup(self) -> None:
        await self.planner_state.async_load()

    async def async_flush_state(self) -> None:
        await self.planner_state.async_save_now()

    # --- entity-facing setters (bypass the config-entry reload path) ---

    async def async_set_target_soc(self, value: float) -> None:
        self.planner_state.target_soc = value
        self.planner_state.async_save()
        await self.async_request_refresh()

    async def async_set_target_datetime(self, value: datetime) -> None:
        self.planner_state.target_datetime = value.isoformat()
        self._cached_prices = []
        self._last_price_fetch = None
        self.planner_state.async_save()
        await self.async_request_refresh()

    async def async_set_rhythm_days(self, value: int) -> None:
        self.planner_state.rhythm_days = value
        self.planner_state.async_save()
        await self.async_request_refresh()

    async def async_set_battery_capacity_kwh(self, value: float) -> None:
        self.planner_state.battery_capacity_kwh = value
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
        self._advance_target_if_elapsed(now)

        current_soc = _get_float_state(self.hass, self._config[CONF_SOC_SENSOR])
        is_charging = _get_bool_state(self.hass, self._config.get(CONF_CHARGING_STATUS_SENSOR))
        plugged_in = _get_bool_state(self.hass, self._config.get(CONF_PLUGGED_IN_SENSOR))
        energy_added = _get_float_state(self.hass, self._config.get(CONF_ENERGY_ADDED_SENSOR))

        if is_charging is not None:
            capacity_estimator.process_charging_edge(
                self.planner_state, self._was_charging, is_charging, current_soc, energy_added
            )
            self._was_charging = is_charging

        target_dt = self._target_datetime()
        if target_dt is not None:
            await self._maybe_fetch_prices(now, target_dt)

        plan = self._compute_plan(now, target_dt, current_soc)
        self.planner_state.plan = _plan_to_dict(plan)

        await self._actuate_switch(plan, current_soc, plugged_in, now, target_dt)

        return {
            "current_soc": current_soc,
            "is_charging": is_charging,
            "plugged_in": plugged_in,
            "target_soc": self.planner_state.target_soc,
            "target_datetime": target_dt,
            "rhythm_days": self.planner_state.rhythm_days,
            "battery_capacity_kwh": self.planner_state.battery_capacity_kwh,
            "capacity_sample_count": len(self.planner_state.capacity_samples),
            "master_switch_on": self.planner_state.master_switch_on,
            "plan": plan,
        }

    def _target_datetime(self) -> datetime | None:
        if self.planner_state.target_datetime is None:
            return None
        return dt_util.parse_datetime(self.planner_state.target_datetime)

    def _advance_target_if_elapsed(self, now: datetime) -> None:
        """Roll the target forward by the rhythm once its time has passed.

        Loops (not a single +=) so a target left unattended past several
        rhythm periods (e.g. the addon was reloaded/HA was down) lands on
        the next one still in the future, not one still stuck in the past.
        """
        target_dt = self._target_datetime()
        if target_dt is None:
            return
        advanced = False
        while now >= target_dt:
            target_dt = target_dt + timedelta(days=self.planner_state.rhythm_days)
            advanced = True
        if advanced:
            _LOGGER.debug("Target elapsed, advancing to %s", target_dt.isoformat())
            self.planner_state.target_datetime = target_dt.isoformat()
            self._cached_prices = []
            self._last_price_fetch = None
            self.planner_state.async_save()

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
        except Exception:  # noqa: BLE001 - a failed fetch must not crash the cycle
            _LOGGER.exception("Failed to fetch prices")
        finally:
            self._last_price_fetch = now

    def _compute_plan(
        self, now: datetime, target_dt: datetime | None, current_soc: float | None
    ) -> ChargePlan:
        if target_dt is None or current_soc is None:
            return ChargePlan(
                slots=[], estimated_cost_eur=0.0, estimated_completion=None,
                target_reachable=None, required_slot_count=0, available_slot_count=0,
            )
        return compute_plan(
            now=now,
            target_datetime=target_dt,
            target_soc=self.planner_state.target_soc,
            current_soc=current_soc,
            battery_capacity_kwh=self.planner_state.battery_capacity_kwh,
            charge_power_kw=self._config[CONF_CHARGE_POWER_KW],
            price_points=self._cached_prices,
        )

    async def _actuate_switch(
        self,
        plan: ChargePlan,
        current_soc: float | None,
        plugged_in: bool | None,
        now: datetime,
        target_dt: datetime | None,
    ) -> None:
        if not self.planner_state.master_switch_on:
            return  # hands-off: don't touch the charge switch at all

        desired_on = self._decide_desired_state(plan, current_soc, plugged_in, now, target_dt)

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
        plugged_in: bool | None,
        now: datetime,
        target_dt: datetime | None,
    ) -> bool:
        if current_soc is not None and current_soc >= self.planner_state.target_soc:
            return False
        if plugged_in is False:
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
