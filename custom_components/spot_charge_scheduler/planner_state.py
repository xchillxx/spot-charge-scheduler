"""Live planning state: target SoC/time, rhythm, capacity calibration, and
the currently computed plan.

Deliberately NOT stored in the config entry's `entry.data` the way SLS's
number entities do it: those trigger a full integration reload on every
write (fine for rarely-changed setup fields like "which switch entity",
wrong for a value like "target SoC" that's meant to be tweaked freely and
must survive across an active charge session without interrupting it). This
lives in its own Store instead, and entities write straight into the
in-memory dict + ask the coordinator to recompute — no reload involved.
"""
from __future__ import annotations

from statistics import median
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DEFAULT_RHYTHM_DAYS,
    DEFAULT_TARGET_SOC,
    MAX_CALIBRATION_SAMPLES,
    MIN_CALIBRATION_SAMPLES_TO_TRUST,
)

STORAGE_VERSION = 1
SAVE_DELAY_SECONDS = 10


class PlannerState:
    """Mutable planning state for one config entry, persisted via Store."""

    def __init__(self, hass: HomeAssistant, entry_id: str, default_capacity_kwh: float) -> None:
        self._store: Store = Store(hass, STORAGE_VERSION, f"spot_charge_scheduler_{entry_id}")
        self._default_capacity_kwh = default_capacity_kwh
        self.target_soc: float = DEFAULT_TARGET_SOC
        self.target_datetime: str | None = None  # ISO string, set by the user on first use
        self.rhythm_days: int = DEFAULT_RHYTHM_DAYS
        self.battery_capacity_kwh: float = default_capacity_kwh
        self.capacity_samples: list[float] = []
        self.master_switch_on: bool = False
        # Charge-session edge tracking for capacity calibration (see
        # capacity_estimator.py) — None when no session is currently open.
        self.session_start_soc: float | None = None
        self.session_start_energy_added: float | None = None
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
        self.target_soc = data.get("target_soc", self.target_soc)
        self.target_datetime = data.get("target_datetime")
        self.rhythm_days = data.get("rhythm_days", self.rhythm_days)
        self.battery_capacity_kwh = data.get("battery_capacity_kwh", self._default_capacity_kwh)
        self.capacity_samples = data.get("capacity_samples", [])
        self.master_switch_on = data.get("master_switch_on", False)
        self.session_start_soc = data.get("session_start_soc")
        self.session_start_energy_added = data.get("session_start_energy_added")
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

    def async_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)

    async def async_save_now(self) -> None:
        await self._store.async_save(self._data_to_save())

    def _data_to_save(self) -> dict:
        return {
            "target_soc": self.target_soc,
            "target_datetime": self.target_datetime,
            "rhythm_days": self.rhythm_days,
            "battery_capacity_kwh": self.battery_capacity_kwh,
            "capacity_samples": self.capacity_samples,
            "master_switch_on": self.master_switch_on,
            "session_start_soc": self.session_start_soc,
            "session_start_energy_added": self.session_start_energy_added,
            "plan": self.plan,
        }
