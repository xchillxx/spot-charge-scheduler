"""Price providers.

Only Tibber is implemented. It's kept behind this small abstract interface
(rather than calling the tibber service directly from the coordinator) so a
second source — e.g. a generic day-ahead/EPEX sensor for households without
Tibber — can be added later as another class here without touching planning
logic in coordinator.py, which only ever deals in PricePoint lists.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

# Tibber (and every quarter-hourly market this integration has seen) quotes
# prices in fixed 15-minute blocks — used to turn a list of slot start times
# into a covered-until timestamp and to size required-slot-count math.
SLOT_DURATION = timedelta(minutes=15)


@dataclass(frozen=True)
class PricePoint:
    start: datetime
    price: float


class PriceProvider(ABC):
    @abstractmethod
    async def async_get_prices(self, hass: HomeAssistant, start: datetime, end: datetime) -> list[PricePoint]:
        """Return known price slots in [start, end), sorted by start time.

        May return fewer slots than the requested range covers — e.g.
        tomorrow's prices simply don't exist yet before Tibber publishes
        them (typically mid-afternoon). Callers must not assume the
        returned list reaches `end`.
        """


class TibberPriceProvider(PriceProvider):
    def __init__(self, home_nickname: str) -> None:
        self._home_nickname = home_nickname

    async def async_get_prices(self, hass: HomeAssistant, start: datetime, end: datetime) -> list[PricePoint]:
        response = await hass.services.async_call(
            "tibber",
            "get_prices",
            {"start": start.isoformat(), "end": end.isoformat()},
            blocking=True,
            return_response=True,
        )
        prices_by_home = (response or {}).get("prices", {})
        if self._home_nickname not in prices_by_home:
            raise HomeAssistantError(
                f"Tibber home '{self._home_nickname}' not found in get_prices response "
                f"(available: {list(prices_by_home)})"
            )
        return sorted(
            (
                PricePoint(start=datetime.fromisoformat(p["start_time"]), price=p["price"])
                for p in prices_by_home[self._home_nickname]
            ),
            key=lambda p: p.start,
        )


def get_price_provider(price_source: str, tibber_home_nickname: str) -> PriceProvider:
    if price_source == "tibber":
        return TibberPriceProvider(tibber_home_nickname)
    raise ValueError(f"Unknown price source: {price_source}")
