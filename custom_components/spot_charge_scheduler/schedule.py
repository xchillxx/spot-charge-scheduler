"""Pure logic for expanding charge-target "cycles" (recurring or one-off)
into concrete occurrences, with a per-occurrence override/exception layer
on top — the same model any calendar app uses for "move/delete just this
one instance of a recurring event" without touching the series itself.

No HA imports beyond dt_util (a pure timezone helper) — kept independent of
the coordinator/calendar entity so the expansion math is easy to reason
about and test in isolation, the same way planner.py is.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .const import ACTIVE_LOOKAHEAD_DAYS, ACTIVE_LOOKBACK_DAYS, DEFAULT_TARGET_SOC

_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_RRULE_FREQ_RE = re.compile(r"FREQ=(DAILY|WEEKLY)", re.IGNORECASE)
_RRULE_INTERVAL_RE = re.compile(r"INTERVAL=(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Occurrence:
    cycle_id: str
    original_start: datetime  # anchor-aligned; stable override key, never the dragged time
    start: datetime  # actual (possibly overridden/dragged) start
    target_soc: float
    summary: str
    rhythm_days: int  # 0 = one-off


def new_cycle_id() -> str:
    return uuid.uuid4().hex


def override_key(cycle_id: str, original_start: datetime) -> str:
    return f"{cycle_id}::{original_start.isoformat()}"


def parse_override_key(key: str) -> tuple[str, datetime] | None:
    """Inverse of override_key — used to recover (cycle_id, original_start)
    from a calendar event's uid, which is always exactly this key (see
    calendar.py). Returns None if key isn't in the expected shape (e.g. a
    stale uid from before a data reset)."""
    cycle_id, _, original_start_iso = key.partition("::")
    if not original_start_iso:
        return None
    original_start = dt_util.parse_datetime(original_start_iso)
    if original_start is None:
        return None
    return cycle_id, original_start


def parse_target_soc_from_text(*texts: str | None, default: float = DEFAULT_TARGET_SOC) -> float:
    """Home Assistant's calendar create/edit UI has no custom numeric field,
    so the target SoC rides along in the event title/description as a
    plain "NN%" — the first one found across the given texts wins."""
    for text in texts:
        if not text:
            continue
        match = _PERCENT_RE.search(text)
        if match:
            return float(match.group(1).replace(",", "."))
    return default


def rhythm_days_from_rrule(rrule: str | None) -> int:
    """Only FREQ=DAILY/WEEKLY with a plain INTERVAL is understood — this
    integration's whole recurrence model is "every N days", nothing
    fancier (no BYDAY, no COUNT/UNTIL end date). Anything else falls back
    to one-off rather than silently misinterpreting a rule it can't
    actually honor."""
    if not rrule:
        return 0
    freq_match = _RRULE_FREQ_RE.search(rrule)
    if not freq_match:
        return 0
    interval_match = _RRULE_INTERVAL_RE.search(rrule)
    interval = int(interval_match.group(1)) if interval_match else 1
    return interval * 7 if freq_match.group(1).upper() == "WEEKLY" else interval


def _expand_cycle(
    cycle: dict, overrides: dict, window_start: datetime, window_end: datetime
) -> list[Occurrence]:
    anchor = dt_util.parse_datetime(cycle["anchor"])
    if anchor is None:
        return []
    rhythm = int(cycle.get("rhythm_days") or 0)
    cycle_id = cycle["id"]
    target_soc = float(cycle["target_soc"])
    summary = cycle.get("summary") or f"Ladeziel {target_soc:g}%"

    candidates: list[datetime] = []
    if rhythm > 0:
        step = timedelta(days=rhythm)
        original_start = anchor
        if original_start < window_start:
            # Jump most of the way there in one step instead of iterating
            # day-by-day from a possibly long-past anchor.
            periods = (window_start - anchor) // step
            original_start = anchor + step * periods
        while original_start < window_start:
            original_start += step
        while original_start <= window_end:
            candidates.append(original_start)
            original_start += step
    elif window_start <= anchor <= window_end:
        candidates.append(anchor)

    occurrences = []
    for original_start in candidates:
        override = overrides.get(override_key(cycle_id, original_start))
        if override and override.get("deleted"):
            continue
        start = original_start
        if override and override.get("start"):
            parsed = dt_util.parse_datetime(override["start"])
            if parsed is not None:
                start = parsed
        occurrences.append(Occurrence(cycle_id, original_start, start, target_soc, summary, rhythm))
    return occurrences


def expand_all(
    cycles: list[dict], overrides: dict, window_start: datetime, window_end: datetime
) -> list[Occurrence]:
    result: list[Occurrence] = []
    for cycle in cycles:
        result.extend(_expand_cycle(cycle, overrides, window_start, window_end))
    result.sort(key=lambda o: o.start)
    return result


def find_active_occurrence(
    cycles: list[dict], overrides: dict, now: datetime, current_soc: float | None
) -> Occurrence | None:
    """The occurrence the coordinator should be planning/charging toward
    right now: the earliest still-unmet one.

    Occurrences are checked in chronological order, so a past one whose
    deadline already passed is checked before any future one — it stays
    "active" (triggering the deadline-blown fallback in coordinator.py)
    until its own target_soc is actually reached, at which point it's
    treated as resolved and the next earliest occurrence takes over. This
    is what lets several independent recurring cycles interleave correctly
    without any explicit "advance to next" bookkeeping — it's always just
    derived fresh from cycles + overrides + the current time and SoC.
    """
    window_start = now - timedelta(days=ACTIVE_LOOKBACK_DAYS)
    window_end = now + timedelta(days=ACTIVE_LOOKAHEAD_DAYS)
    for occ in expand_all(cycles, overrides, window_start, window_end):
        if occ.start >= now:
            return occ
        if current_soc is None or current_soc < occ.target_soc:
            return occ
    return None
