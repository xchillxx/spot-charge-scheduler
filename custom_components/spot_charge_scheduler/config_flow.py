"""Config flow: one-step setup + an options flow with the same schema for
later edits (entities/prices only — the live target SoC/time/rhythm live in
their own number/datetime entities, not here, see planner_state.py)."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CAPACITY_KWH_DEFAULT,
    CONF_CHARGE_POWER_KW,
    CONF_CHARGE_SWITCH,
    CONF_CHARGING_STATUS_SENSOR,
    CONF_ENERGY_ADDED_SENSOR,
    CONF_PLUGGED_IN_SENSOR,
    CONF_PRICE_SOURCE,
    CONF_SOC_SENSOR,
    CONF_TIBBER_HOME_NICKNAME,
    DOMAIN,
    PRICE_SOURCE_TIBBER,
    PRICE_SOURCES,
)


def _default(d: dict, key: str) -> dict:
    """Only apply default= when a real value exists — see surplus-load-switch's
    identical helper for why (an empty default fails EntitySelector validation)."""
    value = d.get(key)
    return {"default": value} if value is not None else {}


def _schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema({
        vol.Required(CONF_CHARGE_SWITCH, **_default(d, CONF_CHARGE_SWITCH)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        ),
        vol.Required(CONF_SOC_SENSOR, **_default(d, CONF_SOC_SENSOR)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="battery")
        ),
        vol.Optional(
            CONF_CHARGING_STATUS_SENSOR, **_default(d, CONF_CHARGING_STATUS_SENSOR)
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
        vol.Optional(
            CONF_PLUGGED_IN_SENSOR, **_default(d, CONF_PLUGGED_IN_SENSOR)
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
        vol.Optional(
            CONF_ENERGY_ADDED_SENSOR, **_default(d, CONF_ENERGY_ADDED_SENSOR)
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_CHARGE_POWER_KW, default=d.get(CONF_CHARGE_POWER_KW, 3.7)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.1, max=50, step=0.1, unit_of_measurement="kW")
        ),
        vol.Required(
            CONF_PRICE_SOURCE, default=d.get(CONF_PRICE_SOURCE, PRICE_SOURCE_TIBBER)
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(options=PRICE_SOURCES, translation_key="price_source")
        ),
        vol.Required(
            CONF_TIBBER_HOME_NICKNAME, **_default(d, CONF_TIBBER_HOME_NICKNAME)
        ): selector.TextSelector(),
        vol.Required(
            CONF_BATTERY_CAPACITY_KWH_DEFAULT, default=d.get(CONF_BATTERY_CAPACITY_KWH_DEFAULT, 60)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=200, step=0.1, unit_of_measurement="kWh")
        ),
    })


class SpotChargeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            return self.async_create_entry(title="Spot Charge Scheduler", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema(), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SpotChargeOptionsFlow()


class SpotChargeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
