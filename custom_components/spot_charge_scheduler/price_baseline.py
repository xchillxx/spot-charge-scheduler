"""Estimates a 'typical price for this time of day' baseline from
recently-observed prices, so the coordinator can judge whether today's
currently-known prices are unusually expensive (worth waiting for fuller
data before charging) or already reasonable — see readiness.py.

Builds its own rolling archive from every price fetch this integration
already makes (planner_state.price_history), rather than depending on a
specific price-sensor entity's recorder history — no extra config field,
no coupling to how any particular price integration names its sensors.
Starts empty and matures over about PRICE_HISTORY_LOOKBACK_DAYS days.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any

from .const import (
    PRICE_HISTORY_LOOKBACK_DAYS,
    PRICE_HISTORY_MIN_SAMPLES,
    PRICE_HISTORY_RETENTION_DAYS,
    PRICE_HISTORY_TIME_TOLERANCE_MINUTES,
)
from .price_source import PricePoint


def merge_observations(
    history: list[dict[str, Any]], new_points: list[PricePoint], now: datetime
) -> list[dict[str, Any]]:
    """Fold freshly fetched price points into the archive, deduplicated by
    start time and pruned to PRICE_HISTORY_RETENTION_DAYS. Tibber prices
    don't change after publication, so a later fetch overwriting an
    existing entry for the same slot is just idempotent re-merging, never
    a "which one is right" conflict."""
    by_start = {h["start"]: h["price"] for h in history}
    for p in new_points:
        by_start[p.start.isoformat()] = p.price
    cutoff = now - timedelta(days=PRICE_HISTORY_RETENTION_DAYS)
    merged = [
        {"start": start, "price": price}
        for start, price in by_start.items()
        if datetime.fromisoformat(start) >= cutoff
    ]
    merged.sort(key=lambda x: x["start"])
    return merged


def typical_price_for_time_of_day(
    history: list[dict[str, Any]], reference: datetime
) -> float | None:
    """Median observed price at roughly this time of day over the last
    PRICE_HISTORY_LOOKBACK_DAYS days. None if there isn't enough history
    yet — callers must treat that as 'no opinion', not 'price is bad'."""
    target_minutes = reference.hour * 60 + reference.minute
    matches = []
    for entry in history:
        start = datetime.fromisoformat(entry["start"])
        age_days = (reference.date() - start.date()).days
        if not (0 < age_days <= PRICE_HISTORY_LOOKBACK_DAYS):
            continue
        minutes = start.hour * 60 + start.minute
        if abs(minutes - target_minutes) <= PRICE_HISTORY_TIME_TOLERANCE_MINUTES:
            matches.append(entry["price"])
    if len(matches) < PRICE_HISTORY_MIN_SAMPLES:
        return None
    return median(matches)
