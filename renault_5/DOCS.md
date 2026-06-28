# Renault 5 app

Logs in to your **My Renault** account, reads your car's data (battery, charging, location,
climate) every few minutes, and publishes it to Home Assistant — you enter your login once
on the Configuration page, no files to edit.

Polls your **Renault 5 E-Tech** through the Renault/Kamereon API and publishes its data
to Home Assistant via MQTT auto-discovery.

**What this app is really for.** Its primary purpose is to be an **updated, maintained
data layer** for the Renault 5 — a drop-in replacement for the fragile `venv` +
`renault-api` CLI + shell-script layer behind [Topolino65](https://github.com/Topolino65)'s
[renault-5-dashboard-view](https://github.com/Topolino65/renault-5-dashboard-view). Entity
names deliberately follow that project (modernised, locale-aware), so **Topolino's own UI
binds straight to these `sensor.r5_*` entities** — keep using his dashboards and just let
this app feed them fresh data.

**The bundled dashboards are a bonus, not the point.** The app can also auto-deploy a
dashboard for you (`deploy_dashboard`) — a **modified version of Topolino's UI**, adapted
from the maintainer's [Alpine A290 app](https://github.com/MatthewHobbs/a290-ha-addon)
(the R5 and A290 share the same Renault EV platform). Use it if you want a ready-made layout,
or ignore it and keep Topolino's — either way the data comes from this app.

## Before you start — install these first

The app only needs the **Mosquitto broker** add-on (its MQTT connection is auto-discovered
from it). It publishes the entities; **you choose the dashboard**:

- **Topolino's [renault-5-dashboard-view](https://github.com/Topolino65/renault-5-dashboard-view)** —
  this app's entities follow that project's naming, so its dashboards bind straight to them.
- **or** one of this app's **bundled dashboards** — set `deploy_dashboard` to `standard`,
  `bubble`, or `both` (off by default).

Either way, if you use a card-based dashboard you must **first install its frontend cards via
HACS → Frontend** — otherwise it renders as *"Custom element doesn't exist"* with broken tiles.
For the bundled dashboards:

| Install via HACS → Frontend | Needed for |
| --- | --- |
| **card-mod** + **Mushroom** | both bundled dashboards |
| **Button Card** | **both** bundled dashboards |
| **Browser Mod** | pop-ups on the **standard** bundled dashboard |
| **Bubble Card** | the **bubble** bundled dashboard only |

Install Mosquitto (and, if you enable a bundled dashboard, the cards above) **before first
start**, so everything renders correctly the first time.

### Finding your VIN and account id

- **VIN** (required): the 17-character vehicle identification number — on your **My Renault**
  app (vehicle details), your registration document (V5C), or the windscreen base. Enter it
  in **uppercase**.
- **account id** (optional): leave it **blank** and the app auto-discovers your
  My Renault/Kamereon account on login. Only set it if you have multiple accounts and need to
  pin a specific one.

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
| `debug_dump` | `true` logs every readable API endpoint to the app Log **once per restart** (IDs/secrets/location redacted, best-effort). It may still contain personal data — do not paste the log publicly. Off by default. |
| `deploy_dashboard` | `none` (default), `standard`, `bubble`, or `both`. Off by default so the app stays a neutral data layer (use Topolino's dashboards, or set this to install a bundled one). Auto-installs the chosen dashboard(s) for you (CDN assets — nothing to copy into `/config/www`). Install the HACS cards first. With `both`, the standard dashboard lands at `dashboard_url_path` and the bubble one at the same path with `-bubble` appended. |
| `dashboard_url_path` | URL slug for the deployed dashboard (default `renault-5`). With `deploy_dashboard: both` the bubble dashboard is installed at `<this>-bubble` (e.g. `renault-5-bubble`). |
| `redeploy_dashboard` | `true` re-pushes the dashboard config on next start. Default `false`. |
| `car_render` | The trim/colour render shown on the dashboard (e.g. `midnight-blue-iconic`), used when auto-deploying. Default `pop-yellow-techno`. See [Customising](https://github.com/MatthewHobbs/r5-ha-addon/tree/main/renault_5/dashboards/CUSTOMISING.md). |

## Status panel

The app adds a **read-only "Renault 5" panel to the Home Assistant sidebar**. It shows
the latest poll at a glance — battery, range, charging, plug, climate, charge limits
and diagnostics — without needing a dashboard. It is **read-only** (it never changes anything),
**auth-gated by Home Assistant**, and stores no credentials or precise location. The bundled
dashboards remain the richer view; the panel is the quick glance.

## Requirements

- The **Mosquitto broker** add-on (the MQTT connection is auto-discovered).

## Entities

Published via MQTT discovery under the **R5** device (entity_ids are built by Home
Assistant from the device name + the friendly name, so they read `sensor.r5_<name>`).
Names follow the Topolino project (minus the legacy `_api`/`_mi` suffixes):

- Battery: `sensor.r5_battery_level`, `…_battery_autonomy`, `…_available_energy`,
  `…_battery_temperature`, `…_battery_last_activity`.
- Charging: `…_charger_status`, `…_charger_plug_status`, `…_charging_flap_status`,
  `…_charging_rate`, `…_charging_remaining_time`, `binary_sensor.r5_charging`.
- Climate: `…_cabin_temperature`, `…_outside_temperature`, `…_hvac_status`,
  `…_hvac_soc_threshold`, `…_preconditioning_temperature`, heated seat/wheel binaries.
- Trip / location: `…_vehicle_mileage`, `device_tracker.r5_location`,
  `…_gps_last_activity`.
- Charge limits (writable sliders, set via `set_battery_soc`):
  `number.r5_soc_min_target` (15–45 %), `number.r5_soc_max_target` (55–100 %).
- Last charge: `…_last_charge_start/end`, `…_start_soc/end_soc`,
  `…_start_energy/end_energy`, `…_duration`, `…_soc_recovered/energy_recovered`,
  `…_average_power`, `…_type`.
- Health: `binary_sensor.r5_api_auth_failure`, `…_data_stale`, `…_plug_state_suspect`.
- Actions (6 native buttons — **no Home Assistant `renault` integration required**):
  **Start Charging**, **Flash Lights**, **Sound Horn**, **Start Air Conditioner**,
  **Stop Air Conditioner**, **Refresh Location** (`button.r5_start_charging` /
  `…_flash_lights` / `…_sound_horn` / `…_start_air_conditioner` /
  `…_stop_air_conditioner` / `…_refresh_location`). Each is gated on
  `supports_endpoint()`, so it only appears where the platform allows it.

### Cabin temperature

The R5's HVAC (climate — heating / air-con) endpoint populates `internalTemperature`, but
often only shortly after HVAC/preconditioning activity — so `sensor.r5_cabin_temperature`
may read unavailable between sessions. That's expected.

### Self-contained — no Home Assistant `renault` integration

Every control on the dashboards is sent **natively** by the app: Start Charging, Flash
Lights, Sound Horn, HVAC Start, HVAC Stop and Refresh Location. You do **not** need Home Assistant's `renault` integration installed. The only thing the platform doesn't expose is
**charge-stop**, so there's no charge-stop button. (HVAC-stop works but, per the platform,
can be flaky.)

The optional **test-mode** preview and **pretty-location** sensor are separate HA helper
packages (not Home Assistant's `renault` integration) — install them only if you want those extras.

### Kamereon account id

Leave `account_id` blank and the app auto-discovers your My Renault/Kamereon account on
login. Only set it if you have multiple accounts and need to pin a specific one.
