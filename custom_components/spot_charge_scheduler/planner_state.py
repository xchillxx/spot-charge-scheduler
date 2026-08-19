"""Live planning state: charge cycles (recurring or one-off), per-occurrence
overrides, capacity/power calibration, and the currently computed plan.

Deliberately NOT stored in the config entry's `entry.data` the way SLS's
number entities do it: those trigger a full integration reload on every
write (fine for rarely-changed setup fields like "which switch entity",
wrong for something meant to be edited freely — dragging a calendar event
around must not interrupt an active charge session via a reload). This
lives in its own Store instead, and entities/the calendar write straight
into the in-memory dict + ask the coordinator to recompute — no reload
involved.

A "cycle" is a charge-target definition: target SoC, an anchor date/time,
and an optional repeat interval in days (0/None = one-off, e.g. an ad-hoc
"I need to leave early" addition). See schedule.py for how cycles get
expanded into concrete calendar occurrences and how a per-occurrence
override (a dragged/rescheduled single instance) is layered on top without
disturbing the rest of the series — the same exception model any calendar
app uses for "edit this occurrence only".
"""
from __future__ import annotations

from statistics import median
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import MAX_CALIBRATION_SAMPLES, MIN_CALIBRATION_SAMPLES_TO_TRUST

STORAGE_VERSION = 1  # unchanged on purpose — see async_load, old/new fields
# coexist fine via .get()-with-default; no async_migrate_func needed, and
# bumping the version without one risks Store raising on the existing live
# Store file instead of just handing back the old dict.
SAVE_DELAY_SECONDS = 10


class PlannerState:
    """Mutable planning state for one config entry, persisted via Store."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, default_capacity_kwh: float, default_charge_power_kw: float
    ) -> None:
        self._store: Store = Store(hass, STORAGE_VERSION, f"spot_charge_scheduler_{entry_id}")
        self._default_capacity_kwh = default_capacity_kwh
        self._default_charge_power_kw = default_charge_power_kw
        # Each: {id, summary, target_soc, anchor (ISO datetime), rhythm_days
        # (int, 0 = one-off), enabled}. See schedule.py for expansion into
        # occurrences.
        self.cycles: list[dict[str, Any]] = []
        # Keyed by f"{cycle_id}::{original_occurrence_start_iso}" (the
        # *unshifted* anchor-aligned time — stable so a re-dragged event
        # updates the same override instead of accumulating duplicates).
        # Value: {"start": new_start_iso | None, "deleted": bool}.
        self.occurrence_overrides: dict[str, dict[str, Any]] = {}
        self.battery_capacity_kwh: float = default_capacity_kwh
        self.capacity_samples: list[float] = []
        self.charge_power_kw: float = default_charge_power_kw
        self.power_samples: list[float] = []
        self.master_switch_on: bool = False
        # Charge-session edge tracking for capacity/power calibration (see
        # capacity_estimator.py) — None/empty when no session is open.
        self.session_start_soc: float | None = None
        self.session_start_energy_added: float | None = None
        self.session_power_readings: list[float] = []
        # Rolling per-slot price archive: [{"start": iso, "price": float}],
        # built up by the coordinator from its own price fetches — see
        # price_baseline.py.
        self.price_history: list[dict[str, Any]] = []
        # Most recently computed plan (coordinator.py owns recomputing this;
        # this class only stores/persists the result for the sensor to read).
        self.plan: dict[str, Any] = {
            "slots": [],
            "estimated_cost_eur": None,
            "estimated_completion": None,
            "target_reachable": None,
            "computed_at": None,
        }

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        self.cycles = data.get("cycles", [])
        self.occurrence_overrides = data.get("occurrence_overrides", {})
        self.battery_capacity_kwh = data.get("battery_capacity_kwh", self._default_capacity_kwh)
        self.capacity_samples = data.get("capacity_samples", [])
        self.charge_power_kw = data.get("charge_power_kw", self._default_charge_power_kw)
        self.power_samples = data.get("power_samples", [])
        self.master_switch_on = data.get("master_switch_on", False)
        self.session_start_soc = data.get("session_start_soc")
        self.session_start_energy_added = data.get("session_start_energy_added")
        self.session_power_readings = data.get("session_power_readings", [])
        self.price_history = data.get("price_history", [])
        self.plan = data.get("plan", self.plan)

    def add_calibration_sample(self, implied_capacity_kwh: float) -> None:
        """Record one session's implied capacity and refresh the estimate.

        Median of the last MAX_CALIBRATION_SAMPLES, not a mean — one
        glitched session (e.g. a sensor hiccup mid-charge) shouldn't swing
        the number the way it would in an average.
        """
        self.capacity_samples.append(implied_capacity_kwh)
        self.capacity_samples = self.capacity_samples[-MAX_CALIBRATION_SAMPLES:]
        if len(self.capacity_samples) >= MIN_CALIBRATION_SAMPLES_TO_TRUST:
            self.battery_capacity_kwh = round(median(self.capacity_samples), 2)
        self.async_save()

    def add_power_sample(self, observed_power_kw: float) -> None:
        """Deliberately a running MAXIMUM, not a median-of-samples like
        add_calibration_sample: charging power has a hard physical ceiling
        (cable/breaker/car limits), and most sessions happen under
        PV-surplus charging, which only ever ties actual power BELOW that
        ceiling — never above it. A median over recent sessions drifts
        down during a stretch of low-surplus days even though the
        hardware's real capability hasn't changed at all (live-confirmed:
        3 low-surplus sessions pulled the estimate down to 4 kW from a
        true ~10.9 kW). The highest peak ever observed is always at least
        as trustworthy as any more recent-but-lower one, so the estimate
        only ever moves up when something faster is genuinely seen, never
        down just because conditions were poor lately."""
        self.power_samples.append(observed_power_kw)
        self.power_samples = self.power_samples[-MAX_CALIBRATION_SAMPLES:]
        self.charge_power_kw = round(max(self.charge_power_kw, observed_power_kw), 2)
        self.async_save()

    def async_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)

    async def async_save_now(self) -> None:
        await self._store.async_save(self._data_to_save())

    def _data_to_save(self) -> dict:
        return {
            "cycles": self.cycles,
            "occurrence_overrides": self.occurrence_overrides,
            "battery_capacity_kwh": self.battery_capacity_kwh,
            "capacity_samples": self.capacity_samples,
            "charge_power_kw": self.charge_power_kw,
            "power_samples": self.power_samples,
            "master_switch_on": self.master_switch_on,
            "session_start_soc": self.session_start_soc,
            "session_start_energy_added": self.session_start_energy_added,
            "session_power_readings": self.session_power_readings,
            "price_history": self.price_history,
            "plan": self.plan,
        }
