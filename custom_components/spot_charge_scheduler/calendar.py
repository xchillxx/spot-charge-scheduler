"""Read-only calendar: a month-view overview of every enabled cycle slot's
upcoming charge targets, so you can see at a glance when the car is
scheduled to be charged. Editing happens through the per-slot entities on
the dashboard (name / target SoC / time / rhythm / on-off), not here — the
calendar deliberately exposes no create/update/delete (that model, with
its per-occurrence override layer, proved too fiddly; see schedule.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import schedule
from .const import CALENDAR_MAX_LOOKAHEAD_DAYS, DOMAIN
from .coordinator import SpotChargeCoordinator
from .device import hub_device_info

# A charge target is a point in time, not a duration — a short block just
# makes it visible/clickable in the calendar grid.
OCCURRENCE_DISPLAY_DURATION = timedelta(minutes=30)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpotChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ChargeScheduleCalendar(coordinator, entry)])


def _to_calendar_event(occ: schedule.Occurrence) -> CalendarEvent:
    summary = occ.name
    if occ.rhythm_days:
        summary = f"{summary} (alle {occ.rhythm_days}d)"
    summary = f"{summary} · {occ.target_soc:g}%"
    description = f"Ziel-SoC: {occ.target_soc:g}%"
    if occ.rhythm_days:
        description += f" · alle {occ.rhythm_days} Tage"
    return CalendarEvent(
        start=occ.start,
        end=occ.start + OCCURRENCE_DISPLAY_DURATION,
        summary=summary,
        description=description,
        uid=f"slot-{occ.slot}-{occ.start.isoformat()}",
    )


class ChargeScheduleCalendar(CoordinatorEntity[SpotChargeCoordinator], CalendarEntity):
    _attr_has_entity_name = True
    _attr_name = "Ladeplan-Kalender"
    _attr_icon = "mdi:calendar-clock"
    # No CalendarEntityFeature flags -> read-only in the HA UI.

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_calendar"

    @property
    def device_info(self):
        return hub_device_info(self._entry.entry_id)

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        occurrences = schedule.expand_all(
            self.coordinator.planner_state.cycle_slots,
            now,
            now + timedelta(days=CALENDAR_MAX_LOOKAHEAD_DAYS),
            now,
        )
        return _to_calendar_event(occurrences[0]) if occurrences else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        occurrences = schedule.expand_all(
            self.coordinator.planner_state.cycle_slots, start_date, end_date, dt_util.now()
        )
        return [_to_calendar_event(occ) for occ in occurrences]
