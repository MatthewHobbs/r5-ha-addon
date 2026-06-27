# Renault 5 — Home Assistant add-on + dashboards

[![CI](https://github.com/MatthewHobbs/r5-ha-addon/actions/workflows/ci.yaml/badge.svg)](https://github.com/MatthewHobbs/r5-ha-addon/actions/workflows/ci.yaml)
[![Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2FMatthewHobbs%2Fr5-ha-addon%2Fmain%2Frenault_5%2Fconfig.yaml&query=%24.version&label=version&color=41BDF5)](renault_5/config.yaml)
[![Coverage](https://img.shields.io/badge/coverage-%E2%89%A590%25-brightgreen)](https://github.com/MatthewHobbs/r5-ha-addon/actions/workflows/ci.yaml)
[![License: MIT](https://img.shields.io/github/license/MatthewHobbs/r5-ha-addon?color=blue)](LICENSE)
[![Home Assistant Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io/addons/)
[![Architectures](https://img.shields.io/badge/arch-amd64%20%7C%20aarch64-informational)](renault_5/config.yaml)

[![Open your Home Assistant instance and add this add-on repository.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FMatthewHobbs%2Fr5-ha-addon)

A maintained Home Assistant add-on for the **Renault 5 E-Tech**. Its real purpose is to be
an **updated data layer** for
[**Topolino65/renault-5-dashboard-view**](https://github.com/Topolino65/renault-5-dashboard-view)
— full credit to Topolino65 for the original dashboards, assets and design.

It replaces that project's fragile `venv` + `renault-api` CLI + shell-script data layer with
a proper **Home Assistant add-on** that polls the Renault/Kamereon API and publishes
`sensor.r5_*` entities over **MQTT auto-discovery** — no scripts, no `secrets.yaml`,
credentials entered once on the add-on's config page. Entity names follow Topolino's naming,
so **his UI binds straight to these entities** — keep using his dashboards and let this
add-on feed them fresh data.

It can **also** auto-deploy a dashboard for you (`deploy_dashboard`, off by default) — a
**modified version of Topolino's UI**, ported from the maintainer's
[Alpine A290 add-on](https://github.com/MatthewHobbs/a290-ha-addon) (the R5 E-Tech and Alpine
A290 share the CMF-BEV / KCM platform). It's a bonus, not the point: use it, or keep
Topolino's own.

Every control is sent **natively** by the add-on (charge, lights, horn, HVAC, refresh
location) — **you do not need Home Assistant's `renault` integration**.

> [!IMPORTANT]
> **Untested on an actual Renault 5 — I don't own one.** I drive an **Alpine A290**, and
> this add-on is back-ported from the [A290 add-on](https://github.com/MatthewHobbs/a290-ha-addon),
> which *does* work on my car. The R5 E-Tech and A290 share the CMF-BEV / KCM platform, so
> this **should** work on an R5 — but I can't verify it on real hardware. Please give it a
> go and **[open an issue](https://github.com/MatthewHobbs/r5-ha-addon/issues)** to let me
> know whether it does or doesn't. I'm very happy to work with an R5 owner to fix anything
> that doesn't.

## What's here

- **The add-on:** [`renault_5/`](renault_5/) — the MQTT data layer + control buttons.
  See [`renault_5/DOCS.md`](renault_5/DOCS.md) for the full entity/option list.
- **The dashboards:** [`dashboards/`](dashboards/) — a **standard** dashboard
  (`front-end.txt`) and a **Bubble Card** dashboard (`front-end-bubble.txt`), both fed by
  the add-on. The add-on can install either — or **both** — for you
  (`deploy_dashboard: standard|bubble|both`), or copy them in manually. Assets (R5 renders,
  map markers, Zen Dots font) live under `dashboards/`. To show the render matching your
  trim/colour, see
  **[Customising your R5](dashboards/CUSTOMISING.md)**. Both are built for phones and
  [**verified on the top mobile devices**](docs/dashboards-on-mobile.md) by CI.

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
| **Mushroom** (`piitaya/lovelace-mushroom`) | most **standard** tiles; a few on Bubble | ✅ Required (both) |
| **Button Card** (`custom-cards/button-card`) | tiles on the **standard** dashboard (one on Bubble) | ✅ Required (both) |
| **Browser Mod** (`nielsfaber/browser_mod`) | the tap-to-open diagnostic pop-ups on the **standard** dashboard | ✅ Standard (pop-ups) |
| **Bubble Card** (`Clooos/Bubble-Card`) | the **Bubble** dashboard (incl. its own pop-ups) | ◻️ Bubble only |

The car's location uses Home Assistant's **built-in `map` card** — no map plugin or API
key needed.

### Optional (not required to run)

- **Test-mode preview** — the charge-simulation panels (`input_boolean.r5_test_mode`,
  `sensor.r5_test_*`, `binary_sensor.r5_test_show_panel`, `button.r5_test_charge_run`).
  A small HA helper/template package; without it those tiles read *unavailable*. (Port
  from the upstream `Packages/`/`Templates/`/`Helpers/`.)
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
4. **Get a dashboard:** set `deploy_dashboard` to `standard`, `bubble`, or `both` and
   restart the add-on (it installs the dashboard + assets via CDN, nothing to copy), **or**
   copy `dashboards/front-end*.txt` into a new dashboard's raw config manually. With `both`,
   the standard dashboard lands at `dashboard_url_path` and the bubble one at that path with
   `-bubble` appended (e.g. `renault-5-bubble`).

## What it provides

- **Sensors:** battery level/autonomy/temperature, charge rate/remaining/flap/plug/status,
  cabin + outside temperature, HVAC status/threshold, preconditioning, SoC min/max,
  mileage, last-charge stats, GPS/HVAC/battery last-activity, and health
  (`api_auth_failure`, `data_stale`, `plug_state_suspect`).
- **Location:** `device_tracker.r5_location`.
- **Native controls (no Home Assistant `renault` integration):** `button.r5_start_charging`,
  `…_flash_lights`, `…_sound_horn`, `…_start_air_conditioner`, `…_stop_air_conditioner`,
  `…_refresh_location` — each gated on what the platform supports.
- **Debug:** set `debug_dump: true` to log every readable API endpoint (secrets redacted)
  to the add-on Log — the safe way to inspect the API (unlike `log_level: debug`, which the
  library uses to print access tokens).

## Renault 5 API support

What the Renault 5 E-Tech (model `R5E1VE`) exposes through the Renault/Kamereon API. The
add-on probes `supports_endpoint()` at startup and only publishes what's available, so a
control the platform forbids is never shown.

| Feature | Endpoint | Renault 5 |
| --- | --- | --- |
| Battery / charge / plug status | `battery-status` | ✅ |
| Mileage | `cockpit` | ✅ |
| HVAC + outside temperature | `hvac-status` | ✅ |
| Charge target / min SoC | `soc-levels` | ✅ |
| Preconditioning + heated seats | `ev/settings` (`charge-schedule`) | ✅ |
| GPS location | `location` | ✅ |
| Start charging | `actions/charge-start` | ✅ (KCM via-settings) |
| Sound horn | `actions/horn-start` | ✅ |
| Flash lights | `actions/lights-start` | ✅ |
| Start / stop climate | `actions/hvac-start` · `hvac-stop` | ✅ |
| Refresh location | `actions/refresh-location` | ✅ |
| Stop charging | `actions/charge-stop` | ❌ not exposed — stop at the charger |
| Tyre pressure (TPMS) | `pressure` | ❌ forbidden |
| Charge mode | `charge-mode` | ❌ forbidden |

✅ supported · ❌ Renault forbids it (or doesn't expose it) on the R5

> Unlike the [Alpine A290](https://github.com/MatthewHobbs/a290-ha-addon) this is ported
> from — where Renault forbids remote charge-start — the **R5 ships a genuine Start
> Charging button and a genuine Refresh Location**. Set `debug_dump: true` to log the
> decoded response of every readable endpoint (secrets redacted) if Renault changes what
> the platform exposes.

## Credits

Original dashboards, assets (car renders, map markers, fonts) and concept by
[**Topolino65**](https://github.com/Topolino65)
([renault-5-dashboard-view](https://github.com/Topolino65/renault-5-dashboard-view)),
MIT-licensed. This project is an independently-maintained continuation; the original
copyright is retained in [LICENSE](LICENSE).
