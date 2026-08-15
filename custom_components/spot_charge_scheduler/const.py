"""Constants for Spot Charge Scheduler."""
from __future__ import annotations

DOMAIN = "spot_charge_scheduler"
PLATFORMS = ["sensor", "number", "switch", "calendar"]

UPDATE_INTERVAL_SECONDS = 60

# How often (minimum spacing) we're willing to call a price-source's fetch,
# even if our cached data doesn't yet cover the target — avoids hammering
# the service every 60s while waiting for tomorrow's prices to be published.
PRICE_FETCH_MIN_INTERVAL_SECONDS = 15 * 60

# Below this SoC delta (%), a charge session is too short/noisy to trust for
# capacity calibration (e.g. a session interrupted seconds after starting).
MIN_CALIBRATION_DELTA_SOC = 5.0
# Number of most-recent calibration samples kept; the estimate is their
# median, so one bad session can't skew it the way a mean would.
MAX_CALIBRATION_SAMPLES = 20
# Calibration only takes over from the config-provided default once this
# many independent sessions have been observed.
MIN_CALIBRATION_SAMPLES_TO_TRUST = 3

# Config-entry keys (set via config flow, edited via options flow) — these
# describe *which entities* the integration talks to, not the live planning
# state (target SoC/time/etc. live in a separate Store, see planner_state.py,
# so editing them doesn't reload the whole integration mid-session).
CONF_CHARGE_SWITCH = "charge_switch_entity"
CONF_SOC_SENSOR = "soc_sensor_entity"
CONF_CHARGING_STATUS_SENSOR = "charging_status_sensor_entity"
CONF_PLUGGED_IN_SENSOR = "plugged_in_sensor_entity"
CONF_ENERGY_ADDED_SENSOR = "energy_added_sensor_entity"
# Both optional together: without a tracker, or without a zone picked, no
# location gating happens at all (same opt-out philosophy as the other
# optional sensors) — set both to only charge while the vehicle is inside
# the chosen zone, e.g. so a charge slot starting while the car is out
# doesn't just do nothing to a switch entity that isn't even connected to
# anything at that location.
CONF_LOCATION_TRACKER_ENTITY = "location_tracker_entity"
CONF_HOME_ZONE_ENTITY = "home_zone_entity"
CONF_CHARGE_POWER_KW = "charge_power_kw"
CONF_PRICE_SOURCE = "price_source"
CONF_TIBBER_HOME_NICKNAME = "tibber_home_nickname"
CONF_BATTERY_CAPACITY_KWH_DEFAULT = "battery_capacity_kwh_default"

PRICE_SOURCE_TIBBER = "tibber"
# Only Tibber is implemented today (see price_source.py) — kept as a select
# rather than hardcoded so a second provider (e.g. a generic day-ahead/EPEX
# sensor) can be added later without changing the config flow shape.
PRICE_SOURCES = [PRICE_SOURCE_TIBBER]

DEFAULT_TARGET_SOC = 50.0
# Fallback when a calendar event's title doesn't contain a parseable "NN%"
# (see schedule.py's parse_target_soc_from_title) — e.g. an event created
# via a plain "+ add event" with no title at all.

# How far back/forward from "now" to expand cycle occurrences when looking
# for the currently active target (schedule.find_active_occurrence). The
# backward half catches a just-passed, still-unmet deadline (the "charge
# anyway, deadline blown" fallback); the forward half only needs to reach
# the next occurrence, but a generous window costs nothing since expansion
# is pure in-memory arithmetic, not I/O.
ACTIVE_LOOKBACK_DAYS = 14
ACTIVE_LOOKAHEAD_DAYS = 60
# How far ahead the calendar entity expands occurrences for display when
# Home Assistant doesn't constrain the query itself (defensive cap).
CALENDAR_MAX_LOOKAHEAD_DAYS = 365
