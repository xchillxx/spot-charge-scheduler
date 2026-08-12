"""Self-calibrating battery capacity: derives real kWh capacity from actual
charge sessions (energy added ÷ SoC delta) instead of relying solely on the
config-entered estimate, which is easy to get wrong (trim/pack variants
aren't reported anywhere in the vehicle's own entities).

Mirrors the calibration philosophy already used for base-load learning in
the surplus-load-switch integration: derive it from lived data, keep a
robust rolling estimate, never trust a single sample.
"""
from __future__ import annotations

import logging

from .const import MIN_CALIBRATION_DELTA_SOC
from .planner_state import PlannerState

_LOGGER = logging.getLogger(__name__)


def process_charging_edge(
    planner_state: PlannerState,
    was_charging: bool | None,
    is_charging: bool,
    current_soc: float | None,
    current_energy_added: float | None,
) -> None:
    """Call once per coordinator cycle with the latest charging/SoC/energy readings.

    was_charging is None on the very first cycle after (re)start — treated
    as "unknown", so a session already in progress at startup is picked up
    as a fresh start rather than guessed at.
    """
    if current_soc is None:
        return

    session_just_started = is_charging and not was_charging
    session_just_ended = was_charging and not is_charging

    if session_just_started:
        planner_state.session_start_soc = current_soc
        planner_state.session_start_energy_added = current_energy_added
        planner_state.async_save()
        return

    if session_just_ended:
        start_soc = planner_state.session_start_soc
        start_energy = planner_state.session_start_energy_added
        planner_state.session_start_soc = None
        planner_state.session_start_energy_added = None

        if start_soc is None or start_energy is None or current_energy_added is None:
            planner_state.async_save()
            return

        delta_soc = current_soc - start_soc
        delta_energy = current_energy_added - start_energy

        if delta_soc < MIN_CALIBRATION_DELTA_SOC or delta_energy <= 0:
            _LOGGER.debug(
                "Skipping capacity calibration sample: delta_soc=%.1f delta_energy=%.2f (too small/invalid)",
                delta_soc,
                delta_energy,
            )
            planner_state.async_save()
            return

        implied_capacity = delta_energy / (delta_soc / 100)
        _LOGGER.debug(
            "Charge session ended: delta_soc=%.1f delta_energy=%.2f kWh -> implied capacity %.2f kWh",
            delta_soc,
            delta_energy,
            implied_capacity,
        )
        planner_state.add_calibration_sample(implied_capacity)
