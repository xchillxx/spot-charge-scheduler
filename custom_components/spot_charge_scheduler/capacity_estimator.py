"""Self-calibrating battery capacity and charging power: derives both from
actual charge sessions (energy added ÷ SoC delta; peak observed power)
instead of relying solely on config-entered guesses, which are easy to get
wrong (trim/pack variants aren't reported anywhere in the vehicle's own
entities, and real-world charging power depends on cable/breaker/amp
settings this integration doesn't control).

Power uses each session's *peak* reading, then the running MAXIMUM across
sessions — neither a median, unlike capacity. Most observed sessions
happen under PV-surplus charging (a completely separate system — see
coordinator.py's module docstring on never auto-switching against it),
which deliberately throttles current to whatever solar surplus is
available *for that entire session* — anywhere from ~1 kW to the
charger's real max. A median-of-sessions approach (tried first, then
live-reverted) drifts down across a stretch of low-surplus days even
though the hardware's ceiling hasn't changed; see planner_state.py's
add_power_sample for the reasoning on why "highest ever seen" is the
physically correct estimator here, not "typical recently".
"""
from __future__ import annotations

import logging

from .const import MIN_CALIBRATION_DELTA_SOC, MIN_POWER_READINGS_PER_SESSION
from .planner_state import PlannerState

_LOGGER = logging.getLogger(__name__)


def process_charging_edge(
    planner_state: PlannerState,
    was_charging: bool | None,
    is_charging: bool,
    current_soc: float | None,
    current_energy_added: float | None,
    current_power_kw: float | None,
) -> None:
    """Call once per coordinator cycle with the latest charging/SoC/energy/
    power readings.

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
        planner_state.session_power_readings = []
        planner_state.async_save()
        return

    if is_charging and current_power_kw is not None and current_power_kw > 0:
        planner_state.session_power_readings.append(current_power_kw)

    if session_just_ended:
        start_soc = planner_state.session_start_soc
        start_energy = planner_state.session_start_energy_added
        power_readings = planner_state.session_power_readings
        planner_state.session_start_soc = None
        planner_state.session_start_energy_added = None
        planner_state.session_power_readings = []

        if len(power_readings) >= MIN_POWER_READINGS_PER_SESSION:
            observed_power = max(power_readings)
            _LOGGER.debug(
                "Charge session ended: %d power readings -> peak %.2f kW",
                len(power_readings),
                observed_power,
            )
            planner_state.add_power_sample(observed_power)

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
