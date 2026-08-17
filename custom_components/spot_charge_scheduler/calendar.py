"""Calendar entity: shows every charge-target cycle (recurring or one-off)
as events, and lets Home Assistant's own calendar UI create/drag/delete
them — that UI's drag-to-reschedule and its "Repeat" picker on event
creation are what actually deliver the "movable, recurring" requirement,
this entity just needs to speak the calendar entity protocol correctly.

Field names below match what HA's frontend actually sends (verified
against home-assistant/core's calendar component: both event creation and
update go through the RFC5545-style WEBSOCKET_EVENT_SCHEMA — dtstart/dtend/
summary/description/rrule — not the older calendar.create_event service's
start_date_time/end_date_time names). A couple of those service-style keys
are still accepted as a fallback for anyone calling the service directly.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import schedule
from .const import CALENDAR_MAX_LOOKAHEAD_DAYS, DOMAIN
from .coordinator import SpotChargeCoordinator
from .device import hub_device_info

_LOGGER = logging.getLogger(__name__)

# How long a materialized occurrence's calendar block appears — a charge
# target is a point in time, not a duration; a short block just makes it
# visible/clickable in the calendar grid.
OCCURRENCE_DISPLAY_DURATION = timedelta(minutes=30)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpotChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ChargeScheduleCalendar(coordinator, entry)])


def _extract_datetime(data: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, datetime):
            return dt_util.as_local(value)
        if isinstance(value, date):
            return dt_util.as_local(datetime.combine(value, datetime.min.time()))
    return None


class ChargeScheduleCalendar(CoordinatorEntity[SpotChargeCoordinator], CalendarEntity):
    _attr_has_entity_name = True
    _attr_name = "Ladeplan-Kalender"
    _attr_icon = "mdi:calendar-clock"
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )

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
            self.coordinator.planner_state.cycles,
            self.coordinator.planner_state.occurrence_overrides,
            now,
            now + timedelta(days=CALENDAR_MAX_LOOKAHEAD_DAYS),
        )
        if not occurrences:
            return None
        return _to_calendar_event(occurrences[0])

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        occurrences = schedule.expand_all(
            self.coordinator.planner_state.cycles,
            self.coordinator.planner_state.occurrence_overrides,
            start_date,
            end_date,
        )
        return [_to_calendar_event(occ) for occ in occurrences]

    async def async_create_event(self, **kwargs: Any) -> None:
        start = _extract_datetime(kwargs, "dtstart", "start_date_time", "start_date")
        if start is None:
            _LOGGER.warning("Ignoring calendar event creation with no start time: %s", kwargs)
            return
        summary = kwargs.get("summary") or ""
        description = kwargs.get("description") or ""
        target_soc = schedule.parse_target_soc_from_text(summary, description)
        rhythm_days = schedule.rhythm_days_from_rrule(kwargs.get("rrule"))
        await self.coordinator.async_add_cycle(start, target_soc, rhythm_days, summary)

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        parsed = schedule.parse_override_key(uid)
        if parsed is None:
            _LOGGER.warning("Ignoring update for unrecognized calendar event uid: %s", uid)
            return
        cycle_id, original_start = parsed
        new_start = _extract_datetime(event, "dtstart", "start_date_time", "start_date")
        if new_start is None:
            _LOGGER.warning("Ignoring calendar event update with no start time: %s", event)
            return
        await self.coordinator.async_set_occurrence_start(cycle_id, original_start, new_start)

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        parsed = schedule.parse_override_key(uid)
        if parsed is None:
            _LOGGER.warning("Ignoring delete for unrecognized calendar event uid: %s", uid)
            return
        cycle_id, original_start = parsed
        await self.coordinator.async_delete_occurrence(cycle_id, original_start)


def _to_calendar_event(occ: schedule.Occurrence) -> CalendarEvent:
    summary = occ.summary
    if occ.rhythm_days:
        summary = f"{summary} (alle {occ.rhythm_days}d)"
    description = f"Ziel-SoC: {occ.target_soc:g}%"
    if occ.rhythm_days:
        description += f" · wiederholt sich alle {occ.rhythm_days} Tage"
    return CalendarEvent(
        start=occ.start,
        end=occ.start + OCCURRENCE_DISPLAY_DURATION,
        summary=summary,
        description=description,
        uid=schedule.override_key(occ.cycle_id, occ.original_start),
    )
