"""Pure logic for turning the fixed set of editable "cycle slots" (see
planner_state.py) into concrete charge-target occurrences.

Deliberately tiny and free of HA imports beyond dt_util (a pure timezone
helper), the same way planner.py is — easy to reason about and unit-test.

A slot is a dict:
    {slot, enabled, name, target_soc, time ("HH:MM"), rhythm_days, anchor}
`rhythm_days == 0` means one-off; otherwise it repeats every N days from
`anchor` (which the coordinator re-bases to "now" whenever the slot is
switched on or its rhythm changes — "ab jetzt alle N Tage"). Pausing a
slot for a holiday is just `enabled = False`; the slot keeps all its
settings and resumes cleanly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .const import (
    ACTIVE_LOOKAHEAD_DAYS,
    ACTIVE_LOOKBACK_DAYS,
    DEFAULT_SLOT_TIME,
    DEFAULT_TARGET_SOC,
    MISSED_DEADLINE_GRACE_HOURS,
)


@dataclass(frozen=True)
class Occurrence:
    slot: int
    start: datetime
    target_soc: float
    name: str
    rhythm_days: int  # 0 = one-off


def parse_hhmm(value: str | None) -> tuple[int, int]:
    """'HH:MM' -> (hour, minute); anything unparseable -> DEFAULT_SLOT_TIME."""
    for candidate in (value, DEFAULT_SLOT_TIME):
        try:
            hh, mm = str(candidate).split(":")[:2]
            h, m = int(hh), int(mm)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h, m
        except (ValueError, AttributeError):
            continue
    return 4, 30


def _slot_name(slot: dict) -> str:
    return (slot.get("name") or "").strip() or f"Zyklus {slot.get('slot', '?')}"


def expand_slot(
    slot: dict, window_start: datetime, window_end: datetime, now: datetime
) -> list[Occurrence]:
    if not slot.get("enabled"):
        return []  # paused (e.g. vacation) or an unused slot

    h, m = parse_hhmm(slot.get("time"))
    try:
        target_soc = float(slot.get("target_soc"))
    except (TypeError, ValueError):
        target_soc = DEFAULT_TARGET_SOC
    rhythm = int(slot.get("rhythm_days") or 0)
    name = _slot_name(slot)

    anchor = dt_util.parse_datetime(slot["anchor"]) if slot.get("anchor") else None
    if anchor is None:
        anchor = now
    base = dt_util.as_local(anchor).replace(hour=h, minute=m, second=0, microsecond=0)

    starts: list[datetime] = []
    if rhythm <= 0:
        # One-off: a single target. If the anchor day's time already sits
        # before "now", roll to the next day so a slot switched on in the
        # afternoon for a "leave at 06:00" trip still means tomorrow 06:00.
        occ = base
        if occ < now:
            occ = occ + timedelta(days=1)
        if window_start <= occ <= window_end:
            starts = [occ]
    else:
        step = timedelta(days=rhythm)
        first = base
        if first < window_start:
            first += step * ((window_start - first) // step)
        while first < window_start:
            first += step
        while first <= window_end:
            starts.append(first)
            first += step

    return [Occurrence(int(slot["slot"]), s, target_soc, name, rhythm) for s in starts]


def expand_all(
    slots: list[dict], window_start: datetime, window_end: datetime, now: datetime
) -> list[Occurrence]:
    result: list[Occurrence] = []
    for slot in slots:
        result.extend(expand_slot(slot, window_start, window_end, now))
    result.sort(key=lambda o: o.start)
    return result


def find_active_occurrence(
    slots: list[dict], now: datetime, current_soc: float | None
) -> Occurrence | None:
    """The occurrence the coordinator should be planning/charging toward
    right now: the earliest still-unmet one across all slots, unless it's
    so overdue (> MISSED_DEADLINE_GRACE_HOURS) that chasing it no longer
    makes sense — in which case the next earliest takes over. No explicit
    "advance to next" bookkeeping; always derived fresh from the slots +
    the clock + the current SoC.
    """
    window_start = now - timedelta(days=ACTIVE_LOOKBACK_DAYS)
    window_end = now + timedelta(days=ACTIVE_LOOKAHEAD_DAYS)
    grace = timedelta(hours=MISSED_DEADLINE_GRACE_HOURS)
    for occ in expand_all(slots, window_start, window_end, now):
        if occ.start >= now:
            return occ
        if now - occ.start > grace:
            continue  # abandoned — too overdue to still be worth chasing
        if current_soc is None or current_soc < occ.target_soc:
            return occ
    return None
