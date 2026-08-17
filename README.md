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

- Charge targets ("cycles") live on a normal Home Assistant **calendar** —
  each is a title with a percentage (e.g. "50%"), a start time, and
  optionally a repeat interval (every N days). That's the whole setup for
  a recurring target — no separate helper entities. Create one via the
  `spot_charge_scheduler.add_cycle` service (Developer Tools → Actions
  auto-generates a proper datetime/percentage/repeat-interval form from
  it) — not the calendar card's own "+" button, which HA core briefly had
  and then regressed (removed between 2026.2.0 and 2026.3.0).
- Any number of cycles can be active at once, recurring or one-off (e.g. an
  ad-hoc "I have to leave unexpectedly" addition) — they interleave
  automatically: whichever one's deadline comes next is what gets planned
  for.
- Drag an event in the calendar to reschedule just that one occurrence —
  the rest of its series is untouched, same as any calendar app's "edit
  this event only". Each cycle also gets its own "Pausiert: …" switch, for
  pausing an entire recurring series at once (e.g. over a vacation) without
  deleting/re-adding it or clicking through every individual occurrence.
- It fetches Tibber's day-ahead 15-minute prices for the window up to
  whichever cycle's deadline is currently active, and schedules the
  cheapest slots that add up to enough charging time — preferring
  contiguous blocks over switching on/off every 15 minutes to chase a
  fraction-of-a-cent difference (gaps of up to 30 min between two selected
  slots get bridged regardless of that gap's own price; relay wear costs
  more than the odd cent).
- If the deadline is close enough that being picky about price would miss
  it, it automatically schedules (near-)continuous charging instead —
  meeting the target always wins over saving money.
- Won't jump on today's cheapest-looking slots if the price data doesn't
  cover the full window yet (e.g. a target tomorrow morning, checked
  before tomorrow's prices are published) — it waits for fuller data
  first, as long as waiting can't itself risk the deadline. It also builds
  its own rolling week of observed prices to judge whether today's visible
  prices are unusually high for this time of day vs. that history, and
  waits longer in that case even once *some* data is technically
  available; a below-typical price is charged on right away instead.
- Once the target SoC is reached, charging stops immediately regardless of
  the remaining schedule, and the next-earliest cycle occurrence becomes
  the new active target.
- Self-calibrating battery capacity **and** charging power estimates:
  instead of trusting the config-entered guesses, it derives real kWh
  capacity (energy added ÷ SoC delta) and real charging power (median
  observed power per session) from your own charge sessions, and uses a
  robust (median) rolling estimate once it has enough of them.
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
| Vehicle location tracker | no | `device_tracker.*`; paired with the zone below, only charges while the vehicle is actually there |
| Home zone | no | any `zone.*`, e.g. `zone.home` — requires the tracker above to have any effect |
| Assumed charging power (kW) | yes | starting value; self-calibrated over time once the sensor below is set |
| Live charging power sensor | no | e.g. `sensor.model_3_charger_power` — a real **power** sensor (kW), not a "rate" sensor (distance/hour); enables power self-calibration |
| Price source | yes | only "Tibber" today |
| Tibber home nickname | yes | as shown in the Tibber app, e.g. "Haus" |
| Battery capacity (kWh) | yes | starting estimate; overwritten automatically once enough real sessions are observed |

All fields are editable later via the integration's "Configure" option.

## Entities

| Entity | Type | Purpose |
|---|---|---|
| Ladeplan-Kalender | `calendar` | Every charge-target cycle, recurring and one-off — create/drag/delete events here directly |
| Akkukapazität | `number` | Capacity used for planning; auto-overwritten by the calibrator |
| Ladeleistung | `number` | Charging power used for planning; auto-overwritten by the calibrator once a power sensor is set |
| Lademodus aktiv | `switch` | Master switch — only while on does this integration touch the charge switch |
| Pausiert: \<cycle summary\> | `switch` | One per cycle, created dynamically — pause/resume an entire recurring series at once |
| Ladeplan | `sensor` | Status (`kein_ziel`/`erreichbar`/`nicht_erreichbar`/`ziel_erreicht`/`nicht_zuhause`/`wartet_auf_daten`) + attributes: active cycle, next slots, estimated cost, estimated completion |
| Nächster Zyklus | `sensor` | Timestamp of the currently active target occurrence |
| Kalibrierte Kapazität | `sensor` | The calibrator's current capacity estimate + how many sessions it's based on |
| Kalibrierte Ladeleistung | `sensor` | The calibrator's current power estimate + how many sessions it's based on |

## Calendar event conventions

The calendar UI has no custom fields, so two pieces of information ride
along in the event itself:
- **Target SoC**: the first `NN%` found in the title or description (e.g.
  "50%", "Ladeziel 80%"). No percentage found → falls back to 50%.
- **Repeat interval**: the calendar's own "Repeat" option, daily or weekly
  with a plain interval (e.g. every 4 days, every 1 week). Anything fancier
  (specific weekdays, an end date) isn't understood and is treated as a
  one-off instead of silently doing something else.

## License

MIT
