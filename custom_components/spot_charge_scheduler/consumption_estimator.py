"""Estimate the EV's real energy use *from the socket* per 100 km straight
out of Home Assistant's long-term statistics: Δ(charge-energy meter) ÷
Δ(odometer) over a rolling window. Same idea as capacity_estimator.py, but
the raw history is already in the recorder — nothing to sample live.

Everything here degrades safe: any missing entity, too little distance in
the window, or an unexpected statistics-API shape just returns None, and the
caller keeps the manually configured EV-consumption value.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import EV_CONSUMPTION_LOOKBACK_DAYS, EV_CONSUMPTION_MIN_KM

_LOGGER = logging.getLogger(__name__)


def _monotonic_span(rows: list[dict]) -> float | None:
    """Increase of a total_increasing series across the rows. Prefers the
    reset-proof `sum`; falls back to the raw `state` (meter reading). None
    if it can't get a positive delta from at least two points."""
    for key in ("sum", "state"):
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        if len(vals) >= 2 and (vals[-1] - vals[0]) > 0:
            return float(vals[-1] - vals[0])
    return None


async def async_estimate_ev_consumption_kwh_100km(
    hass: HomeAssistant, odometer_entity: str | None, charge_energy_entity: str | None
) -> float | None:
    if not odometer_entity or not charge_energy_entity:
        return None
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import (
            statistics_during_period,
        )
    except ImportError:
        return None

    end = dt_util.utcnow()
    start = end - timedelta(days=EV_CONSUMPTION_LOOKBACK_DAYS)
    try:
        stats = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            end,
            {odometer_entity, charge_energy_entity},
            "day",
            None,
            {"state", "sum"},
        )
    except Exception:  # noqa: BLE001 - statistics API varies across HA versions
        _LOGGER.debug("EV-consumption statistics query failed", exc_info=True)
        return None

    km = _monotonic_span(stats.get(odometer_entity) or [])
    kwh = _monotonic_span(stats.get(charge_energy_entity) or [])
    if km is None or kwh is None or km < EV_CONSUMPTION_MIN_KM:
        return None
    return round(kwh / km * 100.0, 2)
