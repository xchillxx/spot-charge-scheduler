"""The manual master switch: only while ON does this integration actuate
the configured charge switch at all — see coordinator._actuate_switch's
hands-off-when-off policy. Deliberately manual only (no auto-detection
against surplus-load-switch) — confirmed with the user: the PV wallbox's
own surplus mode has to be switched off by hand outside HA anyway, so
there's nothing to auto-detect against.

Also one "pause" switch per charge-target cycle, created dynamically as
cycles are added via the calendar/add_cycle service — there's no fixed
number of these, so they're added on the fly rather than all at startup
(the coordinator-listener pattern below), same idea as any integration
whose entity set depends on user-managed, unbounded config data."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SpotChargeCoordinator
from .device import hub_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SpotChargeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MasterSwitch(coordinator, entry)])

    known_cycle_ids: set[str] = set()

    def _add_new_cycle_switches() -> None:
        new_entities = [
            CyclePauseSwitch(coordinator, entry, cycle["id"])
            for cycle in coordinator.planner_state.cycles
            if cycle["id"] not in known_cycle_ids
        ]
        if not new_entities:
            return
        known_cycle_ids.update(e.cycle_id for e in new_entities)
        async_add_entities(new_entities)

    _add_new_cycle_switches()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_cycle_switches))


class MasterSwitch(CoordinatorEntity[SpotChargeCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Lademodus aktiv"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_master_switch"

    @property
    def device_info(self):
        return hub_device_info(self._entry.entry_id)

    @property
    def is_on(self) -> bool:
        return self.coordinator.planner_state.master_switch_on

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_master_switch(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_master_switch(False)


class CyclePauseSwitch(CoordinatorEntity[SpotChargeCoordinator], SwitchEntity):
    """ON = paused. Named after the cycle's own title so 'pause vacation
    cycle' means finding the switch with that summary, not an opaque id.
    Turning the whole series off skips every occurrence (recurring or not)
    until turned back on — see schedule.py's `enabled` check — without
    touching individual occurrence overrides, so a vacation doesn't leave
    behind a pile of manually-deleted single events to undo afterward."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-remove"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SpotChargeCoordinator, entry: ConfigEntry, cycle_id: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self.cycle_id = cycle_id
        self._attr_unique_id = f"{entry.entry_id}_cycle_paused_{cycle_id}"

    @property
    def device_info(self):
        return hub_device_info(self._entry.entry_id)

    def _cycle(self) -> dict | None:
        for cycle in self.coordinator.planner_state.cycles:
            if cycle["id"] == self.cycle_id:
                return cycle
        return None

    @property
    def available(self) -> bool:
        return self._cycle() is not None

    @property
    def name(self) -> str:
        cycle = self._cycle()
        summary = cycle["summary"] if cycle else self.cycle_id
        return f"Pausiert: {summary}"

    @property
    def is_on(self) -> bool:
        cycle = self._cycle()
        return not cycle.get("enabled", True) if cycle else False

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_cycle_enabled(self.cycle_id, False)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_cycle_enabled(self.cycle_id, True)
