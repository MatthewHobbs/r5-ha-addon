# Renault 5 — Home Assistant add-on + dashboards

A maintained Home Assistant integration for the **Renault 5 E-Tech**, continuing the work
of [**Topolino65/renault-5-dashboard-view**](https://github.com/Topolino65/renault-5-dashboard-view)
— full credit to Topolino65 for the original dashboards, assets and design.

This repo modernises that project by replacing its fragile `venv` + `renault-api` CLI +
shell-script data layer with a proper **Home Assistant add-on** that polls the
Renault/Kamereon API and publishes `sensor.r5_*` entities over **MQTT auto-discovery** —
no scripts, no `secrets.yaml`, credentials entered once on the add-on's config page. It is
ported from the [Alpine A290 add-on](https://github.com/MatthewHobbs/a290-ha-addon) (the R5
E-Tech and Alpine A290 share the CMF-BEV / KCM platform).

Every control is sent **natively** by the add-on (charge, lights, horn, HVAC, refresh
location) — **you do not need the official Renault integration**.

## What's here

- **The add-on:** [`renault_5/`](renault_5/) — the MQTT data layer + control buttons.
  See [`renault_5/DOCS.md`](renault_5/DOCS.md) for the full entity/option list.
- **The dashboards:** [`dashboards/`](dashboards/) — a **standard** dashboard
  (`front-end.txt`) and a **Bubble Card** dashboard (`front-end-bubble.txt`), both fed by
  the add-on. The add-on can install either for you (`deploy_dashboard: standard|bubble`),
  or copy them in manually. Assets (R5 renders, map markers, Zen Dots font) live under
  `dashboards/`. To show the render matching your trim/colour, see
  **[Customising your R5](dashboards/CUSTOMISING.md)**.

## Requirements

Install these **before** the dashboards will render correctly.

### Add-ons (Settings → Add-ons)

| Dependency | Why | Required? |
| --- | --- | --- |
| **Mosquitto broker** | The MQTT broker the add-on publishes to (auto-discovered). | ✅ Required |
| **Renault 5** (this repo) | The data layer + control buttons. | ✅ Required |

### Frontend cards (via [HACS](https://hacs.xyz) → Frontend)

The dashboards are built from custom Lovelace cards. Install **HACS** first, then:

| Card (HACS name) | Used by | Required? |
| --- | --- | --- |
| **card-mod** (`thomasloven/lovelace-card-mod`) | styling/fonts on **both** dashboards | ✅ Required (both) |
| **Mushroom** (`piitaya/lovelace-mushroom`) | most tiles on **both** dashboards | ✅ Required (both) |
| **Button Card** (`custom-cards/button-card`) | tiles on the **standard** dashboard | ✅ Standard |
| **Browser Mod** (`nielsfaber/browser_mod`) | the tap-to-open diagnostic pop-ups | ✅ Both (pop-ups) |
| **Bubble Card** (`Clooos/Bubble-Card`) | the **Bubble** dashboard only | ◻️ Bubble only |

The car's location uses Home Assistant's **built-in `map` card** — no map plugin or API
key needed.

### Optional (not required to run)

- **Test-mode preview** — the charge-simulation panels (`input_boolean.r5_test_mode`,
  `sensor.test_*`). A small HA helper/template package; without it those tiles read
  *unavailable*. (Port from the upstream `Packages/`/`Templates/`/`Helpers/`.)
- **Pretty location** — `sensor.r5_pretty_location` ("Driveway / Home / town"), a template
  sensor with an optional [`places`](https://github.com/custom-components/places)
  integration. Without it the location card shows the raw tracker.

## Install

1. **Add the add-on repo:** Settings → Add-ons → Add-on Store → ⋮ → **Repositories**, add
   `https://github.com/MatthewHobbs/r5-ha-addon`. Install **Mosquitto broker** (if needed)
   and the **Renault 5** add-on.
2. **Configure + start:** on the add-on's **Configuration** tab set your My Renault
   `username`/`password`, `vin`, `locale`, and `battery_capacity_kwh`, then **Start**.
   The `sensor.r5_*` / `binary_sensor.r5_*` entities and `button.r5_*` controls appear
   under an **R5** device within a minute.
3. **Install the HACS cards** above (do this *before* deploying a dashboard, or it renders
   as "custom element doesn't exist").
4. **Get a dashboard:** set `deploy_dashboard: standard` or `bubble` and restart the add-on
   (it installs the dashboard + assets via CDN, nothing to copy), **or** copy
   `dashboards/front-end*.txt` into a new dashboard's raw config manually.

## What it provides

- **Sensors:** battery level/autonomy/temperature, charge rate/remaining/flap/plug/status,
  cabin + outside temperature, HVAC status/threshold, preconditioning, SoC min/max,
  mileage, last-charge stats, GPS/HVAC/battery last-activity, and health
  (`api_auth_failure`, `data_stale`, `plug_suspect`).
- **Location:** `device_tracker.r5_location`.
- **Native controls (no official Renault integration):** `button.r5_start_charging`,
  `…_flash_lights`, `…_sound_horn`, `…_start_air_conditioner`, `…_stop_air_conditioner`,
  `…_refresh_location` — each gated on what the platform supports.
- **Debug:** set `debug_dump: true` to log every readable API endpoint (secrets redacted)
  to the add-on Log.

## Credits

Original dashboards, assets (car renders, map markers, fonts) and concept by
[**Topolino65**](https://github.com/Topolino65)
([renault-5-dashboard-view](https://github.com/Topolino65/renault-5-dashboard-view)),
MIT-licensed. This project is an independently-maintained continuation; the original
copyright is retained in [LICENSE](LICENSE).
