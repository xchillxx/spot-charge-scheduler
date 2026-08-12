# Spot Charge Scheduler

Home Assistant custom integration for price-optimized EV charging with a
target SoC/time and a repeating cycle — e.g. "50% charged by 04:00, every
4 days" for a shift schedule. Picks the cheapest 15-minute price slots that
still meet the deadline; if time runs out, it automatically shifts to
charging every remaining slot so the deadline is still met, price be damned.

It's not Tesla- or Tibber-specific by design: every controlled/read entity
(charge switch, SoC sensor, etc.) is picked in the config flow, so it works
with any vehicle integration that exposes a start/stop switch and a SoC
sensor. Today it only reads prices from Tibber's `tibber.get_prices`
service (15-minute resolution, full day-ahead forecast — not the same as
the `sensor.*_electricity_price` state, which only shows the current
price). A generic day-ahead/EPEX price source for non-Tibber households can
be added later behind the same `price_source.py` interface.

## What it does

- You set a target SoC and a target date/time (native `number`/`datetime`
  entities this integration creates — no separate helpers to set up).
- It fetches Tibber's day-ahead 15-minute prices for the window between now
  and your target, and schedules the cheapest slots that add up to enough
  charging time.
- If the deadline is close enough that being picky about price would miss
  it, it automatically schedules (near-)continuous charging instead —
  meeting the target always wins over saving money.
- Once the target SoC is reached, charging stops immediately regardless of
  the remaining schedule.
- After each cycle's target time passes, the next target time is
  automatically set forward by your configured rhythm (e.g. +4 days) — edit
  the SoC/time/rhythm at any point, they're always live.
- A self-calibrating battery capacity estimate: instead of trusting a
  single config value, it derives real kWh capacity from your own charge
  sessions (energy added ÷ SoC delta) and uses a robust (median) rolling
  estimate once it has enough of them.
- A manual master switch. **This integration never auto-detects or
  auto-switches against a PV-surplus charging setup** — flip it on
  yourself once you've switched your wallbox/car out of solar-surplus mode
  for the season.

## Installation

### HACS (custom repository)
1. HACS → Integrations → ⋮ → Custom repositories
2. Add this repository URL, category "Integration"
3. Install "Spot Charge Scheduler", restart Home Assistant

### Manual
Copy `custom_components/spot_charge_scheduler` into your `config/custom_components/` folder and restart.

## Setup

Settings → Devices & Services → Add Integration → "Spot Charge Scheduler". You'll be asked for:

| Field | Required | Notes |
|---|---|---|
| Charge start/stop switch | yes | e.g. `switch.model_3_charger` |
| SoC sensor | yes | e.g. `sensor.model_3_battery`, must be `%` |
| Charging status sensor | no | binary; drives the capacity calibrator's session detection |
| Plugged-in sensor | no | binary; without it, "not connected" is never checked |
| Energy added sensor | no | enables capacity self-calibration |
| Assumed charging power (kW) | yes | used to size how many slots are needed; the actual charge switch's own current/amperage is left untouched |
| Price source | yes | only "Tibber" today |
| Tibber home nickname | yes | as shown in the Tibber app, e.g. "Haus" |
| Battery capacity (kWh) | yes | starting estimate; overwritten automatically once enough real sessions are observed |

All fields are editable later via the integration's "Configure" option.

## Entities

| Entity | Type | Purpose |
|---|---|---|
| Ziel-SoC | `number` | Target state of charge (%) for the current cycle |
| Ziel-Zeitpunkt | `datetime` | Target date/time for the current cycle |
| Wiederhol-Rhythmus | `number` | Days between cycles; auto-advances Ziel-Zeitpunkt once its time has passed |
| Akkukapazität | `number` | Capacity used for planning; auto-overwritten by the calibrator |
| Lademodus aktiv | `switch` | Master switch — only while on does this integration touch the charge switch |
| Ladeplan | `sensor` | Status (`kein_ziel`/`erreichbar`/`nicht_erreichbar`/`ziel_erreicht`) + attributes: next slots, estimated cost, estimated completion |
| Nächster Zyklus | `sensor` | Timestamp of the currently active/next target |
| Kalibrierte Kapazität | `sensor` | The calibrator's current estimate + how many sessions it's based on |

## License

MIT
