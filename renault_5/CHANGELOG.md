# Changelog

## 0.4.0

- **Pick your car's trim/colour from a dropdown** — new `car_render` option (default
  Pop Yellow Techno). When the add-on auto-deploys a dashboard it serves the matching
  render from the repo CDN, so there's nothing to copy or hand-edit. All 22 R5 renders
  (Evolution / Iconic / Roland Garros / Techno × colours) are selectable; change it and
  re-deploy (`redeploy_dashboard: true`) to update. Manual installs can still swap the
  images by hand — see [`dashboards/CUSTOMISING.md`](../dashboards/CUSTOMISING.md).

## 0.3.0

- **Refresh Location button** (`button.r5_refresh_location`) — the R5 supports
  `actions/refresh-location`, so the add-on now asks the car to push a fresh GPS fix.
  Gated like the other controls; appears only where the platform supports it.
- **Debug mode** — set `debug_dump: true` on the Configuration page and each poll logs
  the **full raw payload of every readable API endpoint** (details, battery, soc, cockpit,
  hvac, location, charge-schedule, charge-mode, charging-settings, tyre-pressure,
  lock-status, res-state, contracts, notifications) to the add-on **Log**. Identifiers
  (VIN, account id, registration, contact fields) and your configured username are
  redacted; telemetry is kept. Turn it off when you're done — it's verbose.

## 0.2.0

Ports the A290 add-on's reliability/maintainability improvements so the two stay in sync.
No entity, command-topic or config change — existing dashboards are unaffected.

- **Gate every control button on `supports_endpoint()`** (the new `ACTION_BUTTONS` table).
  A button is published only when the car supports its action endpoint, and any retained
  config is cleared otherwise — so a control the platform forbids never lingers. All five
  (charge-start/horn/lights/HVAC start+stop) are supported on the R5, so this is a safety
  net; it also keeps parity with the A290, where charge-start is forbidden and hidden.
- **Reuse one cached login across polls** (`VehicleSession`) instead of a full Gigya
  re-auth every cycle (~288/day at the default interval). Tokens are refreshed by
  `renault-api`; the cached login is dropped after any failed poll so it self-heals.
- **MQTT command handling is now generic** — a single `renault_5/cmd/#` subscription
  dispatches presses via `COMMAND_ACTIONS`. Same entity IDs and command topics as before.
- **Add a `pytest` suite (`renault_5/tests/`) and a CI Tests job**; pin `paho-mqtt==2.1.0`
  and `PyYAML==6.0.3` (were `>=`) for reproducible builds.

## 0.1.0

- Initial release. A maintained MQTT data layer for the **Renault 5 E-Tech**, ported
  from the Alpine A290 add-on (the two share the CMF-BEV / KCM platform).
- Polls the Renault/Kamereon API via the `renault-api` library — battery-status,
  cockpit, HVAC, location, ev/settings (preconditioning), ev/soc-levels, plus optional
  charge-mode and tyre-pressure (gated on `supports_endpoint`) — and publishes
  `sensor.r5_*` / `binary_sensor.r5_*` entities over **MQTT auto-discovery**. No venv,
  CLI, shell scripts or `secrets.yaml`.
- Entity names follow Topolino65's `renault-5-dashboard-view`, modernised: no legacy
  `_api`/`_mi` suffixes, and **locale-aware units** (miles for `en_GB`, km elsewhere).
- Native command buttons: **Start Charging**, **Flash Lights**, **Sound Horn**, **HVAC
  Start**, **HVAC Stop** — all sent directly via `renault-api`, so the **official Renault
  integration is not required** for any dashboard control. HVAC-start uses the car's
  preconditioning temperature (fallback 21°C). Plus charge-session tracking (last-charge
  stats,
  charge-type classification), plug stuck-detection, and health sensors (auth-failure,
  data-stale). State persists to `/data/state.json`.
- **Cabin Temperature** (`internalTemperature`) is published — the R5 populates it
  (unlike the A290), though often only shortly after HVAC activity.
