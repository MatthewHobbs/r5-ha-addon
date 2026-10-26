# Renault 5 Dashboard — User Guide

This guide explains what the dashboard shows. All data comes from the **Renault 5 app**
over MQTT (`sensor.r5_*`), with no dependency on Home Assistant's `renault` integration. Two styles ship with the app: **standard**
(`front-end.txt`) and **Bubble** (`front-end-bubble.txt`); they show the same data with
different cards. See [INSTALLATION.md](INSTALLATION.md) to deploy one (or `both`).

> The Renault 5 reports `batteryCapacity` as 0, so charge-energy figures are derived from
> the `battery_capacity_kwh` you set on the app. `chargingInstantaneousPower` units can be
> unreliable, and battery temperature can be absent. Cabin temperature remains published;
> whether the R5 supplies a useful value is not established.

## Standard dashboard

Three columns.

### Vehicle Status

A render of the car that **swaps to a charge indicator when a plug is connected** — showing
current State of Charge (SoC — how full the battery is, as a %) against the Min/Target SoC limits. Below it:

| Tile | Entity | Notes |
| --- | --- | --- |
| Battery Level | `sensor.r5_battery_level` | % |
| Available Energy | `…_available_energy` | kWh |
| Range | `…_battery_autonomy` | miles for `en_GB`, km otherwise |
| Mileage | `…_vehicle_mileage` | odometer |
| Plug Status | `…_charger_plug_status` | Connected / Disconnected |
| Charging Status | `…_charger_status` | Charging / Not Charging / Charge Ended / Waiting… / Flap Open / Error |
| Charging Power | `…_charging_rate` | kW |
| Charging Time Remaining | `…_charging_remaining_time` | from the API |
| Charging Flap | `…_charging_flap_status` | Closed / "Open: Plugged In" |
| HVAC Status | `…_hvac_status` | HVAC = the car's climate (heating / air-con) |
| HVAC SoC Threshold | `…_hvac_soc_threshold` | battery % below which HVAC can't start |

### Last Activity · Location · Presets

- **Last Activity** — timestamps of the last reported updates: `…_hvac_last_activity`,
  `…_gps_last_activity`, `…_battery_last_activity`.

  **These stop moving when the car is parked, and that is normal.** The car commits its data at
  **power-off** and then sleeps; polling a sleeping car returns the same trip-end values.
  Measured on a real A290 after a drive ending 12:03 UTC: battery-status 12:03:20, location
  12:02:41, climate 11:46 — all three still unchanged eight hours later, still parked.
  That observation is from the A290, not a measurement on an R5.

  So **Mileage** and **Location** only move after a completed journey, and battery/range/plug
  values only refresh while the car is awake (briefly after power-on, and during a charge).
  Nothing here updates again until the car is driven.
- **Location** — Home Assistant's built-in `map` card, driven by
  `device_tracker.r5_location`. Updates when the car is powered off at the end of a
  journey, and not again until the next one.
- **Climate presets** (read from My Renault / the car): Preconditioning Temperature
  (`…_preconditioning_temperature`), Heated Steering Wheel and Driver/Passenger Seats
  (`binary_sensor.r5_heated_*`).

### Remote Control · Last Charge

- **Remote Control** — native MQTT buttons published by the app (no Home Assistant `renault` integration):
  - `button.r5_sound_horn`, `…_flash_lights`, `…_start_air_conditioner`, `…_stop_air_conditioner`,
    and `…_start_charging`.
  - **Refresh Location is off by default.** On the shared platform it can replace a parked car's
    saved position with "no fix" until the next journey (observed on an A290, not an R5). Set
    `enable_refresh_location: true` if you want it back, with `publish_location: true`.
  - **Start Charging:** the standard dashboard retains a greyed-out tile; Bubble has no active
    charge-start control. The API disables the car's own charge programs; it does not override
    external scheduling such as Octopus Intelligent (inferred for R5 from the shared API path).
    Use Smart Charging's **Charge Now** control when configured for your charger's boost/bump.
    Remote charge-*stop* is unavailable on this model.
  - **Start Air Conditioner** uses `precondition_temperature` when configured, otherwise the car's
    own preconditioning temperature; HVAC can lag while asleep, and stop may be unreliable.
- **Charge limits** — writable sliders, set on the car via `set_battery_soc`:
  `number.r5_soc_max_target` (target, 55–100 %) and
  `number.r5_soc_min_target` (minimum, 15–45 %). Shown only when the car supports the
  `soc-levels` endpoint. These replace the Minimum/Target charge-level numbers from Home Assistant's `renault` integration.
- **Last Charge** — captured by the app at the end of a session:
  Start/End time, Start/End SoC, Start/End Energy, SoC Recovered (%), Energy Recovered (kWh),
  Duration, Average Power, and **Type** — which is either **Home** or **Rapid/Public**
  (decided by whether the average power exceeds a home-charger threshold).

### Health

Four problem indicators: `binary_sensor.r5_api_auth_failure` (bad credentials /
locale), `…_data_stale`, `…_poll_failing`, and `…_plug_state_suspect` (plug state disagrees
with movement/charging — e.g. "Connected" but driven).

`…_data_stale` and `…_poll_failing` answer two different questions, and the difference matters:

- **Data Stale** — the *car* has not reported for longer than `stale_hours`. The readings on
  screen are real but old. A parked car can show this on once that threshold is exceeded.
  Paired with **Poll Failing** off, it reads as "the add-on is working, the car has not reported
  since" — usually because it has not been driven. The dashboard tile shows this as
  **Last Seen** with the time underneath.
- **Poll Failing** — the *add-on* has not reached Renault for longer than `stale_hours`.

They are independent. A car parked in an underground car park goes Data Stale while polling
stays perfectly healthy — that is the common case, and it is the one that matters, because the
battery percentage on the dashboard is then hours or days out of date. `…_battery_last_activity` shows
when the car last reported; `…_last_successful_poll` shows when the add-on last got through.

### Test mode (optional)

If you installed the optional test package (`input_boolean.r5_test_mode` +
`input_button.r5_test_charge_run`), you can simulate a 5-minute charge to preview the charge
panels without a real session. It's off by default and not needed for normal use.

## Bubble dashboard

The same entities and data, styled with **Bubble Card**. It has no active Start Charging control.
Optional Smart Charging controls target your charger's entities when the app's `charger_*`
options are configured, independently of the car's native charge-start command.

---

Spot an error or a tile that doesn't match your car? Open an issue on the
[app repo](https://github.com/MatthewHobbs/r5-ha-addon).
