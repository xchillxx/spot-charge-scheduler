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

from .const import PRICE_BRIDGE_TOLERANCE
from .price_source import SLOT_DURATION, PricePoint

SLOT_HOURS = SLOT_DURATION.total_seconds() / 3600


@dataclass(frozen=True)
class ChargePlan:
    slots: list[PricePoint]  # chronologically sorted, only the selected ones
    estimated_cost_eur: float
    estimated_completion: datetime | None  # when the GUARANTEED floor is covered
    target_reachable: bool | None  # None = no target set, nothing to evaluate yet
    required_slot_count: int  # slots needed for the guaranteed floor only
    available_slot_count: int
    # How many of `slots` are opportunistic top-up (cheap slots taken beyond
    # the guaranteed floor, up to the car's charge limit) rather than
    # mandatory. 0 whenever opportunistic top-up is inactive/unconfigured.
    opportunistic_slot_count: int = 0
    # The SoC charging actually stops at this cycle: the car charge limit
    # when opportunistic top-up is active, otherwise just the target SoC.
    effective_ceiling_soc: float | None = None


def _bridge_gaps_by_price(
    eligible_sorted: list[PricePoint], selected: list[PricePoint], tolerance: float
) -> set[datetime]:
    """Fill in unselected gaps between two selected slots, so the charger
    runs through a couple of merely-slightly-pricier slots instead of
    switching off to save a fraction of a cent. A gap is bridged in full —
    no length limit — as long as EVERY slot in it costs no more than
    `tolerance` above the priciest slot already selected: we're already
    paying up to that price, so the gap isn't a new cost decision, just
    rounding. A genuinely expensive stretch (e.g. an evening peak) stays
    unbridged regardless of length, since some slot in it will exceed the
    tolerance.

    Only looks backward from each confirmed-selected slot to the previous
    one, so a single forward pass is enough — filling a gap only ever
    shrinks later gaps, never creates new ones to reconsider."""
    if not selected:
        return set()
    threshold = max(p.price for p in selected) * (1 + tolerance)
    selected_starts = {p.start for p in selected}
    flags = [p.start in selected_starts for p in eligible_sorted]
    last_selected_index = -1
    for i, is_selected in enumerate(flags):
        if not is_selected:
            continue
        gap_range = range(last_selected_index + 1, i)
        if last_selected_index != -1 and gap_range and all(
            eligible_sorted[k].price <= threshold for k in gap_range
        ):
            for k in gap_range:
                flags[k] = True
        last_selected_index = i
    return {p.start for p, flag in zip(eligible_sorted, flags) if flag}


def compute_plan(
    now: datetime,
    target_datetime: datetime,
    target_soc: float,
    current_soc: float,
    battery_capacity_kwh: float,
    charge_power_kw: float,
    price_points: list[PricePoint],
    max_soc: float | None = None,
    cheap_price_threshold: float | None = None,
) -> ChargePlan:
    """`target_soc` is the guaranteed floor — reached by the deadline no
    matter the price. When `max_soc` and `cheap_price_threshold` are both
    given and `max_soc > target_soc`, the plan ALSO grabs any still-eligible
    slot priced at/below the threshold, up to `max_soc` — opportunistic
    top-up, never forced and never pushing out `estimated_completion` (which
    always reflects only the guaranteed floor)."""
    opportunistic_active = (
        max_soc is not None
        and cheap_price_threshold is not None
        and max_soc > target_soc
    )
    ceiling = max_soc if opportunistic_active else target_soc

    if current_soc >= ceiling:
        return ChargePlan(
            slots=[], estimated_cost_eur=0.0, estimated_completion=now,
            target_reachable=True, required_slot_count=0, available_slot_count=0,
            opportunistic_slot_count=0, effective_ceiling_soc=ceiling,
        )

    slot_kwh = charge_power_kw * SLOT_HOURS

    eligible = [p for p in price_points if now <= p.start < target_datetime]
    available_slot_count = len(eligible)

    if slot_kwh <= 0:
        # Misconfigured charge_power_kw — can't plan at all; surface as
        # unreachable rather than dividing by zero or looping forever.
        return ChargePlan(
            slots=[], estimated_cost_eur=0.0, estimated_completion=None,
            target_reachable=False, required_slot_count=0, available_slot_count=available_slot_count,
            opportunistic_slot_count=0, effective_ceiling_soc=ceiling,
        )

    # --- mandatory: the cheapest N slots needed to hit the guaranteed floor ---
    mandatory_kwh = max(0.0, target_soc - current_soc) / 100 * battery_capacity_kwh
    required_slot_count = math.ceil(mandatory_kwh / slot_kwh) if mandatory_kwh > 0 else 0

    eligible_by_price = sorted(eligible, key=lambda p: p.price)
    if available_slot_count >= required_slot_count:
        mandatory = eligible_by_price[:required_slot_count]
        target_reachable = True
    else:
        mandatory = list(eligible)
        target_reachable = False

    # --- opportunistic: cheap leftover slots, up to the car's charge limit ---
    # Only when the guaranteed floor is actually reachable — if we're already
    # taking every slot just to (try to) hit the floor, there's nothing left
    # to be opportunistic with.
    opportunistic: list[PricePoint] = []
    if opportunistic_active and target_reachable:
        ceiling_kwh = (ceiling - current_soc) / 100 * battery_capacity_kwh
        ceiling_slot_count = math.ceil(ceiling_kwh / slot_kwh)
        bonus_budget = max(0, ceiling_slot_count - len(mandatory))
        mandatory_starts = {p.start for p in mandatory}
        opportunistic = [
            p for p in eligible_by_price
            if p.start not in mandatory_starts and p.price <= cheap_price_threshold
        ][:bonus_budget]

    selected_starts = {p.start for p in mandatory} | {p.start for p in opportunistic}
    eligible_sorted = sorted(eligible, key=lambda p: p.start)
    selected = [p for p in eligible_sorted if p.start in selected_starts]

    bridged_starts = _bridge_gaps_by_price(eligible_sorted, selected, PRICE_BRIDGE_TOLERANCE)
    selected = [p for p in eligible_sorted if p.start in bridged_starts]
    bonus_count = sum(1 for p in selected if p.start not in {m.start for m in mandatory})

    estimated_cost_eur = sum(p.price * slot_kwh for p in selected)
    # Completion tracks the guaranteed floor only — opportunistic slots that
    # land later must not make the promised target look later than it is.
    mandatory_by_start = sorted(mandatory, key=lambda p: p.start)
    if mandatory_by_start:
        estimated_completion = mandatory_by_start[-1].start + SLOT_DURATION
    elif current_soc >= target_soc:
        estimated_completion = now
    else:
        estimated_completion = None

    return ChargePlan(
        slots=selected,
        estimated_cost_eur=round(estimated_cost_eur, 2),
        estimated_completion=estimated_completion,
        target_reachable=target_reachable,
        required_slot_count=required_slot_count,
        available_slot_count=available_slot_count,
        opportunistic_slot_count=bonus_count,
        effective_ceiling_soc=ceiling,
    )
