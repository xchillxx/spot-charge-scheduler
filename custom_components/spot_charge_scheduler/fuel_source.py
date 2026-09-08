"""Fuel-price lookup via Tankerkönig (creativecommons.tankerkoenig.de).

Kept small and defensive like price_source.py: one HTTP call, throttled to
hourly by the coordinator, and a failure never propagates into the update
cycle — the fuel-price sensor just goes unavailable until the next try.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)
_URL = "https://creativecommons.tankerkoenig.de/json/list.php"
_MAX_RADIUS_KM = 25.0  # Tankerkönig's own hard limit


@dataclass(frozen=True)
class FuelPrice:
    price_eur_per_l: float
    station: str
    distance_km: float
    fuel_type: str


async def async_get_cheapest_fuel_price(
    hass: HomeAssistant,
    api_key: str,
    lat: float,
    lng: float,
    radius_km: float,
    fuel_type: str,
) -> FuelPrice | None:
    """Cheapest currently-open station for `fuel_type` within `radius_km` of
    (lat, lng). None if the search returns no usable price; raises on an API
    error so the caller can back off and retry sooner."""
    session = async_get_clientsession(hass)
    params = {
        "lat": f"{lat:.6f}",
        "lng": f"{lng:.6f}",
        "rad": f"{max(1.0, min(radius_km, _MAX_RADIUS_KM)):.1f}",
        "sort": "price",  # requires a concrete `type`, never "all"
        "type": fuel_type,
        "apikey": api_key,
    }
    async with session.get(
        _URL, params=params, timeout=aiohttp.ClientTimeout(total=20)
    ) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)

    if not data.get("ok", False):
        raise RuntimeError(f"Tankerkönig: {data.get('message', 'unbekannter Fehler')}")

    return pick_cheapest(data.get("stations", []) or [], fuel_type)


def pick_cheapest(stations: list[dict], fuel_type: str) -> FuelPrice | None:
    """Cheapest station with a usable price — preferring open ones, falling
    back to any station's last-known price if nothing's open right now.
    Pure/stateless so it can be unit-tested without a network call."""

    def _usable(s: dict, require_open: bool) -> bool:
        price = s.get("price")
        if not isinstance(price, (int, float)) or price <= 0:
            return False
        return bool(s.get("isOpen", False)) if require_open else True

    candidates = [s for s in stations if _usable(s, True)]
    if not candidates:
        candidates = [s for s in stations if _usable(s, False)]
    if not candidates:
        return None

    best = min(candidates, key=lambda s: s["price"])
    return FuelPrice(
        price_eur_per_l=round(float(best["price"]), 3),
        station=str(best.get("brand") or best.get("name") or "?").strip() or "?",
        distance_km=round(float(best.get("dist") or 0.0), 1),
        fuel_type=fuel_type,
    )
