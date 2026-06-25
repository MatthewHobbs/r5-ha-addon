# Renault 5 add-on

Polls your **Renault 5 E-Tech** through the Renault/Kamereon API and publishes its data
to Home Assistant via MQTT auto-discovery — no venv, CLI, shell scripts or `secrets.yaml`.

This is a maintained data layer for Topolino65's
[renault-5-dashboard-view](https://github.com/Topolino65/renault-5-dashboard-view):
entity names follow that project (modernised, locale-aware), so the dashboards bind to
these add-on entities.

## Configuration

| Option | Description |
| --- | --- |
| `username` | Your **My Renault** app email. |
| `password` | Your My Renault app password. |
| `account_id` | Your Kamereon account id. **Optional** — leave blank to auto-discover it. |
| `vin` | Your vehicle VIN (uppercase). |
| `locale` | Pick from the dropdown (e.g. `en_GB`, `fr_FR`, `de_DE`). Sets the API region **and** the drive side — `en_GB`/`en_IE` ⇒ RHD, otherwise LHD (used for heated-seat mapping). Units follow the locale (**miles for `en_GB`**, km elsewhere). |
| `poll_interval` | Seconds between polls (60–3600, default 300). |
| `battery_capacity_kwh` | `52` or `40`. Must be set — the API reports capacity as 0; used to derive charge-session energy. |
| `stale_hours` | Mark data stale after this many hours without a successful poll (default 6). |
| `log_level` | `info` normally; `debug` for troubleshooting. |
| `debug_dump` | `true` logs the full raw payload of every readable API endpoint to the add-on Log each poll (IDs/secrets redacted). Verbose — turn off when done. |
| `deploy_dashboard` | `none` (default), `standard`, or `bubble`. Auto-installs that dashboard for you (CDN assets — nothing to copy into `/config/www`). Install the HACS cards first. |
| `dashboard_url_path` | URL slug for the deployed dashboard (default `renault-5`). |
| `redeploy_dashboard` | `true` re-pushes the dashboard config on next start. Default `false`. |
| `car_render` | The trim/colour render shown on the dashboard (e.g. `midnight-blue-iconic`), used when auto-deploying. Default `pop-yellow-techno`. See [Customising](../dashboards/CUSTOMISING.md). |

## Requirements

- The **Mosquitto broker** add-on (the MQTT connection is auto-discovered).

## Entities

Published via MQTT discovery under the **Renault 5** device. Names follow the Topolino
project (minus the legacy `_api`/`_mi` suffixes):

- Battery: `sensor.r5_battery_level`, `…_battery_autonomy`, `…_available_energy`,
  `…_battery_temperature`, `…_battery_last_activity`.
- Charging: `…_charger_status`, `…_charger_plug_status`, `…_charging_flap_status`,
  `…_charging_rate`, `…_charging_remaining_time`, `binary_sensor.r5_charging`.
- Climate: `…_cabin_temperature`, `…_external_temperature`, `…_hvac_status`,
  `…_hvac_soc_threshold`, `…_preconditioning_temperature`, heated seat/wheel binaries.
- Trip / location: `…_vehicle_mileage`, `device_tracker.renault_5_location`,
  `…_gps_last_activity`.
- SoC: `…_soc_min_target`, `…_soc_max_target`.
- Last charge: `…_last_charge_start/end`, `…_start_soc/end_soc`, `…_duration_min`,
  `…_recovered_pct/recovered_kwh`, `…_average_power`, `…_type`.
- Health: `binary_sensor.r5_api_auth_failure`, `…_data_stale`, `…_plug_suspect`.
- Actions (6 native buttons — **no official Renault integration required**):
  **Start Charging**, **Flash Lights**, **Sound Horn**, **Start Air Conditioner**,
  **Stop Air Conditioner**, **Refresh Location** (`button.r5_start_charging` /
  `…_flash_lights` / `…_sound_horn` / `…_start_air_conditioner` /
  `…_stop_air_conditioner` / `…_refresh_location`). Each is gated on
  `supports_endpoint()`, so it only appears where the platform allows it.

### Cabin temperature

The R5's HVAC endpoint populates `internalTemperature`, but often only shortly after
HVAC/preconditioning activity — so `sensor.r5_cabin_temperature` may read unavailable
between sessions. That's expected.

### Self-contained — no official Renault integration

Every control on the dashboards is sent **natively** by the add-on: Start Charging, Flash
Lights, Sound Horn, HVAC Start and HVAC Stop. You do **not** need the official Renault
integration installed. The only thing the platform doesn't expose is **charge-stop**, so
there's no charge-stop button. (HVAC-stop works but, per the platform, can be flaky.)

The optional **test-mode** preview and **pretty-location** sensor are separate HA helper
packages (not the official integration) — install them only if you want those extras.

### Kamereon account id

Leave `account_id` blank and the add-on auto-discovers your MyRenault/Kamereon account on
login. Only set it if you have multiple accounts and need to pin a specific one.
