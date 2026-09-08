# Spot Charge Scheduler

Home Assistant custom integration for price-optimized EV charging with a
target SoC/time and a repeating cycle — e.g. "65% charged by 04:30, every
4 days" for a shift schedule. Picks the cheapest 15-minute price slots that
still meet the deadline; if time runs out, it automatically shifts to
charging every remaining slot so the deadline is still met, price be damned.

Cycles are a fixed set of **editable slots** — each one a handful of native
entities (on/off, name, target SoC, time, rhythm) you change straight on
the dashboard. No calendar editing, no service calls.

It's not Tesla- or Tibber-specific by design: every controlled/read entity
(charge switch, SoC sensor, etc.) is picked in the config flow, so it works
with any vehicle integration that exposes a start/stop switch and a SoC
sensor. Today it only reads prices from Tibber's `tibber.get_prices`
service (15-minute resolution, full day-ahead forecast — not the same as
the `sensor.*_electricity_price` state, which only shows the current
price). A generic day-ahead/EPEX price source for non-Tibber households can
be added later behind the same `price_source.py` interface.

## What it does

- Charge targets are **6 fixed cycle slots**. Each slot is five native
  entities you edit directly on the dashboard: an on/off switch, a name, a
  target-SoC number, a time, and a rhythm-in-days number (`0` = one-off).
  Fill as many as you need — two shift patterns plus a few spares in
  practice. Nothing else to set up, no calendar editing, no services.
- All slots run at once; the integration always plans for whichever slot's
  next deadline comes first. A `0`-day slot is a one-off for an ad-hoc
  "I have to leave early tomorrow".
- The rhythm counts **from now**: switching a slot on (or changing its
  rhythm) sets "next in N days". To pause a slot for a holiday, just switch
  it off — every setting is kept and it resumes cleanly when you switch it
  back on.
- The **calendar** entity is a read-only month overview of what's coming
  up; you don't edit anything there.
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
- **Opportunistic top-up beyond the target.** Point the config at your
  car's own charge-limit entity (e.g. `number.model_3_charge_limit`) and,
  once the guaranteed target SoC is covered, it will *also* grab any
  remaining slots that are genuinely cheap — priced at or below the Nth
  percentile (default 10, live-adjustable via the "Billig-Schwelle
  (Perzentil)" number; the "Billig-Schwelle" sensor shows what that
  percentile currently is in ct/kWh) of every price *observed over the
  last 8 days* —
  up to that car limit. This is never forced: it never pushes out the
  guaranteed completion time, the past-deadline "charge no matter what"
  fallback still only ever drives to the cycle target, and it stays fully
  inert until you configure the entity and the price archive has a couple
  of days of history. So a cycle target of "50% by 04:00" plus a car limit
  of 80% means *always at least 50%*, and *80% whenever the night is
  actually cheap*.
- Won't jump on today's cheapest-looking slots if the price data doesn't
  cover the full window yet (e.g. a target tomorrow morning, checked
  before tomorrow's prices are published) — it waits for fuller data
  first, as long as waiting can't itself risk the deadline. It also builds
  its own rolling week of observed prices to judge whether today's visible
  prices are unusually high for this time of day vs. that history, and
  waits longer in that case even once *some* data is technically
  available; a below-typical price is charged on right away instead.
- Once the target SoC is reached, charging stops immediately regardless of
  the remaining schedule, and the next-earliest slot occurrence becomes
  the new active target. A deadline that's missed by a lot (more than
  MISSED_DEADLINE_GRACE_HOURS, default 4h) is abandoned the same way
  instead of being chased forever — a shift's 04:30 target is no longer
  worth pursuing at 8 pm that evening if the next shift's own target is
  already coming up.
- Self-calibrating battery capacity **and** charging power estimates:
  instead of trusting the config-entered guesses, it derives real kWh
  capacity (energy added ÷ SoC delta) from your own charge sessions, and a
  robust (median-of-sessions) rolling estimate once it has enough of them.
  Charging power specifically uses each session's *peak* reading, then
  tracks the running MAXIMUM across sessions rather than a median — most
  sessions happen under PV-surplus charging (a separate system, throttled
  to whatever solar is available for that whole session, anywhere from
  ~1 kW up), so a median drifts down across a stretch of low-surplus days
  even though the hardware's real ceiling hasn't changed; the estimate
  only ever moves up when something genuinely faster is observed.
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
| Car charge-limit entity | no | a `number.*` holding the car's own target charge limit (e.g. `number.model_3_charge_limit`); enables opportunistic top-up beyond the cycle target while power is cheap vs. the last 8 days. Unset → feature off |
| Price source | yes | only "Tibber" today |
| Tibber home nickname | yes | as shown in the Tibber app, e.g. "Haus" |
| Battery capacity (kWh) | yes | starting estimate; overwritten automatically once enough real sessions are observed |

All fields are editable later via the integration's "Configure" option.

## Entities

| Entity | Type | Purpose |
|---|---|---|
| Slot N aktiv | `switch` | Cycle slot N on/off. Off = paused (settings kept). One per slot (N = 1…6) |
| Slot N Name | `text` | Free-form label, e.g. "Tagschicht" |
| Slot N Ziel-SoC | `number` | Target state of charge for that slot, 5–100 %, 5 % steps |
| Slot N Uhrzeit | `time` | Time of day the target must be reached by |
| Slot N Rhythmus (Tage) | `number` | Repeat every N days from activation; `0` = one-off |
| Ladeplan-Kalender | `calendar` | **Read-only** month overview of upcoming slot occurrences |
| Akkukapazität | `number` | Capacity used for planning; auto-overwritten by the calibrator |
| Ladeleistung | `number` | Charging power used for planning; auto-overwritten by the calibrator once a power sensor is set |
| Billig-Schwelle (Perzentil) | `number` | How cheap (percentile of the last 8 days' observed prices, default 10) a slot must be before opportunistic top-up takes it; no effect without a car charge-limit entity |
| Billig-Schwelle | `sensor` | What that percentile currently works out to, in ct/kWh, against the last 8 days — plus whether opportunistic top-up is active |
| Lademodus aktiv | `switch` | Master switch — only while on does this integration touch the charge switch |
| Ladeplan | `sensor` | Status (`kein_ziel`/`erreichbar`/`nicht_erreichbar`/`ziel_erreicht`/`opportunistisch`/`nicht_zuhause`/`wartet_auf_daten`) + attributes: active cycle, next slots, estimated cost, estimated completion, opportunistic-slot count, effective ceiling SoC, cheap-price threshold |
| Nächster Zyklus | `sensor` | Timestamp of the currently active target occurrence |
| Kalibrierte Kapazität | `sensor` | The calibrator's current capacity estimate + how many sessions it's based on |
| Kalibrierte Ladeleistung | `sensor` | The calibrator's current power estimate + how many sessions it's based on |

## Upgrading from ≤ 0.12.0

The old model — cycles authored as calendar events, per-occurrence drag
overrides, `add_cycle` / `update_cycle` services, dynamic "Pausiert: …"
switches — is gone. On first start after the upgrade, every **recurring**
cycle you had is migrated into a slot (earliest first); bare one-off
calendar entries are dropped. Re-create any one-offs you still need in a
free slot with rhythm `0`.

## License

MIT
