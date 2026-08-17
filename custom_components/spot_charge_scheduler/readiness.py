"""Decides whether to actually act on a computed plan right now, or hold
off in case fuller/better price data arrives before the deadline forces a
decision anyway. Deliberately separate from planner.py: the plan itself
("what would we do with the data we have") and the readiness decision
("should we act on that yet") are different questions — the dashboard
still shows the plan either way, only actuation is gated by this.
"""
from __future__ import annotations

from datetime import datetime

from .const import DATA_WAIT_SAFETY_BUFFER_HOURS, PRICE_BRIDGE_TOLERANCE
from .planner import SLOT_HOURS


def should_defer_for_better_data(
    now: datetime,
    target_dt: datetime,
    required_slot_count: int,
    data_covers_target: bool,
    best_known_price: float | None,
    historical_typical_price: float | None,
) -> bool:
    """True = hold off actuating and wait; False = go ahead and act on the
    best plan available now.

    Waiting is only ever chosen when it's provably safe: even after
    reserving DATA_WAIT_SAFETY_BUFFER_HOURS for data to show up, there'd
    still be enough time left to do the required charging. That safety
    check alone already answers "is it worth waiting at all" once the
    deadline gets close — no separate override for the deadline-forced
    fallback is needed here, it falls out of the same slack calculation.
    """
    if data_covers_target:
        return False  # nothing left to wait for — we already know everything relevant

    hours_until_target = (target_dt - now).total_seconds() / 3600
    required_hours = required_slot_count * SLOT_HOURS
    slack_hours = hours_until_target - required_hours
    if slack_hours < DATA_WAIT_SAFETY_BUFFER_HOURS:
        return False  # can't risk it — act now on the best information available

    if (
        historical_typical_price is not None
        and best_known_price is not None
        and best_known_price <= historical_typical_price * (1 + PRICE_BRIDGE_TOLERANCE)
    ):
        return False  # today's visible price already looks fine vs. the last week — no reason to wait

    return True
