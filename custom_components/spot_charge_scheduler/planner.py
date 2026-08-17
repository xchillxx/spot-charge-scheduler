"""Pure charge-plan computation — no HA/coordinator dependencies, so it's
straightforward to reason about (and unit-test) in isolation.

Core idea: always take the N cheapest still-eligible 15-minute slots, where
N is however many are needed to cover the remaining energy. This one rule
already implements both halves of the spec's fallback requirement (section
2.5) without a separate "emergency mode":
  - Plenty of time left -> N is small relative to the eligible slot count,
    so only genuinely cheap slots get picked.
  - Time is running out -> N approaches (or exceeds) the eligible slot
    count, so the "cheapest N" naturally becomes "almost all of them", i.e.
    near-continuous charging. If N exceeds what's available, we take every
    remaining eligible slot and flag target_reachable=False so the caller
    knows the deadline can't be hit even at full effort.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .const import MAX_GAP_SLOTS_TO_BRIDGE
from .price_source import SLOT_DURATION, PricePoint

SLOT_HOURS = SLOT_DURATION.total_seconds() / 3600


@dataclass(frozen=True)
class ChargePlan:
    slots: list[PricePoint]  # chronologically sorted, only the selected ones
    estimated_cost_eur: float
    estimated_completion: datetime | None
    target_reachable: bool | None  # None = no target set, nothing to evaluate yet
    required_slot_count: int
    available_slot_count: int


def _bridge_short_gaps(
    eligible_sorted: list[PricePoint], selected_starts: set[datetime], max_gap_slots: int
) -> set[datetime]:
    """Fill in short unselected gaps between two selected slots, so the
    charger runs through a couple of merely-slightly-pricier slots instead
    of switching off for 15-30 min to save a fraction of a cent. Only looks
    backward from each confirmed-selected slot to the previous one, so a
    single forward pass is enough — no gap ever needs re-checking once
    filled, since filling only ever shrinks later gaps, never creates new
    ones to reconsider."""
    selected_flags = [p.start in selected_starts for p in eligible_sorted]
    last_selected_index = -1
    for i, is_selected in enumerate(selected_flags):
        if not is_selected:
            continue
        gap = i - last_selected_index - 1
        if last_selected_index != -1 and 0 < gap <= max_gap_slots:
            for k in range(last_selected_index + 1, i):
                selected_flags[k] = True
        last_selected_index = i
    return {p.start for p, flag in zip(eligible_sorted, selected_flags) if flag}


def compute_plan(
    now: datetime,
    target_datetime: datetime,
    target_soc: float,
    current_soc: float,
    battery_capacity_kwh: float,
    charge_power_kw: float,
    price_points: list[PricePoint],
) -> ChargePlan:
    if current_soc >= target_soc:
        return ChargePlan(
            slots=[], estimated_cost_eur=0.0, estimated_completion=now,
            target_reachable=True, required_slot_count=0, available_slot_count=0,
        )

    remaining_kwh = (target_soc - current_soc) / 100 * battery_capacity_kwh
    slot_kwh = charge_power_kw * SLOT_HOURS

    eligible = [p for p in price_points if now <= p.start < target_datetime]
    available_slot_count = len(eligible)

    if slot_kwh <= 0:
        # Misconfigured charge_power_kw — can't plan at all; surface as
        # unreachable rather than dividing by zero or looping forever.
        return ChargePlan(
            slots=[], estimated_cost_eur=0.0, estimated_completion=None,
            target_reachable=False, required_slot_count=0, available_slot_count=available_slot_count,
        )

    required_slot_count = math.ceil(remaining_kwh / slot_kwh)

    if available_slot_count >= required_slot_count:
        selected = sorted(eligible, key=lambda p: p.price)[:required_slot_count]
        target_reachable = True
    else:
        selected = list(eligible)
        target_reachable = False

    eligible_sorted = sorted(eligible, key=lambda p: p.start)
    bridged_starts = _bridge_short_gaps(
        eligible_sorted, {p.start for p in selected}, MAX_GAP_SLOTS_TO_BRIDGE
    )
    selected = [p for p in eligible_sorted if p.start in bridged_starts]
    estimated_cost_eur = sum(p.price * slot_kwh for p in selected)
    estimated_completion = (selected[-1].start + SLOT_DURATION) if selected else None

    return ChargePlan(
        slots=selected,
        estimated_cost_eur=round(estimated_cost_eur, 2),
        estimated_completion=estimated_completion,
        target_reachable=target_reachable,
        required_slot_count=required_slot_count,
        available_slot_count=available_slot_count,
    )
