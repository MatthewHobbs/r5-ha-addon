# Changelog

## 0.9.0

- **Set the charge limits from Home Assistant.** SOC Min/Max Target are now **writable
  `number` sliders** (`number.r5_soc_min_target` 15–45 %, `number.r5_soc_max_target`
  55–100 %) instead of read-only sensors. Moving a slider writes both limits to the car via
  `renault-api`'s `set_battery_soc` (the `soc-levels` endpoint); the unchanged limit is read
  back first and re-sent. Published only where the car supports `soc-levels`, and optimistic
  so the slider reflects the new value immediately. Ported from the Alpine A290 — keeps the
  two add-ons in sync.
- **`debug_dump` now covers the full endpoint set.** Added the previously-missing readable
  endpoints — `charges` (real charge-session history), `car-adapter` (vehicle spec), the R5-only
  `alerts`, plus `hvac-history` / `hvac-sessions` so the dump documents what's forbidden too.
  Date-ranged (`charges`, `charge-history`) probed over the last 30 days; `alerts` is path-
  resolved and raw-GET. Use it to see what `charges` / `alerts` return before building on them.
- **Container health endpoint + `HEALTHCHECK`** (ported from the A290 — closes a parity gap).
  A tiny `/healthz` server runs on the poll loop and a Dockerfile `HEALTHCHECK` polls it, so a
  **deadlocked event loop** (which the in-loop logic can't catch) now marks the container
  unhealthy and the Supervisor **restarts** it, instead of the add-on silently going stale.
- **Poll-loop robustness (A290 parity).** Each `poll_once` is now wrapped in
  `asyncio.wait_for`, so a single hung API call can't stall the loop indefinitely (it's
  treated as a failed poll and retried with backoff). Charge-limit writes are also
  **serialised** (a lock), so adjusting both sliders quickly can't clobber a limit.

## 0.8.2

- Refine 0.8.1: only drop the `distance` device_class when the unit is **miles**
  (`locale: en_GB`); for km it's kept, so the sensors retain distance semantics/statistics.

## 0.8.1

- Fix **Battery Autonomy / Vehicle Mileage shown in km even with `locale: en_GB`** (ported
  from the A290). Those sensors carried `device_class: distance`, so Home Assistant on a
  metric unit system converted the add-on's miles back to km for display. Dropped the
  `distance` device_class on both so the locale-derived unit shows as-is.

## 0.8.0

- **`deploy_dashboard: both`** — deploy the standard *and* Bubble dashboards in one go. The
  standard dashboard lands at `dashboard_url_path`; the Bubble one at the same path with
  `-bubble` appended (e.g. `renault-5-bubble`). Each is still create-once (left alone unless
  `redeploy_dashboard: true`), and the Zen Dots font resource is registered once per run.
  Brings the R5 add-on to parity with the Alpine A290.

## 0.7.0

Quality + documentation pass.

- **Test coverage raised to ~99%** (was 54%): a new `test_runtime.py` exercises the poll
  loop (success + failure branches), the MQTT plumbing and reconnect callbacks, command
  dispatch + debounce, the debug dump, account resolution, every `poll_once` degradation
  path, and the whole dashboard-deploy WebSocket flow. CI now enforces a **90% floor**
  (`--cov-fail-under=90`) and lints the tests too, so no commit can silently regress below it.
- **Docs corrected against the running add-on.** Home Assistant derives entity_ids from the
  device name + friendly name (the discovery `object_id` is ignored), so DOCS.md now lists
  the real ids — `device_tracker.r5_location` (not `renault_5_location`),
  `sensor.r5_outside_temperature`, `…_last_charge_duration` / `…_soc_recovered` /
  `…_energy_recovered`, and `binary_sensor.r5_plug_state_suspect` — verified against the
  live A290 entity registry. README's HACS-card matrix is fixed (Browser Mod is
  standard-only; Button Card is needed by both) and the test-mode entity names corrected.
- **README shields** — CI status, the enforced coverage floor, Home Assistant add-on,
  supported architectures, and MIT licence.

## 0.6.0

Review-panel P1/P2 follow-ups (reliability + supply-chain hygiene).

- **Atomic state file** — `state.json` is written to a temp file then `os.replace()`d, so a
  kill mid-write never corrupts charge-session history.
- **MQTT resilience** — `on_connect`/`on_disconnect` callbacks re-subscribe and re-announce
  `online` after a broker restart (it went silently stale before); bounded reconnect backoff
  (1–120 s) and a 30 s keepalive.
- **Command debounce** — a repeated button press within 5 s is ignored, so a double-tap can't
  fire the car action twice.
- **Deploy hardening** — the dashboard WebSocket calls have a per-receive timeout (an
  unexpected frame can't hang the deploy); the dashboard fetch ref is now `R5_ADDON_REF`
  (default `main`) so it can be pinned to a release tag for a reproducible deploy.
- **Supply chain** — every GitHub Action in CI is pinned to a commit SHA (was mutable tags).
- **Observability** — the per-poll log now reports how long the poll took.
- **CI** — added a coverage gate (`--cov-fail-under=50`) so coverage can't silently regress.

## 0.5.0

Review-panel fixes (security / privacy / reliability / QA) before first release.

- **Privacy: `debug_dump` hardened.** Dropped the location, contracts and notification-settings
  endpoints from the dump (location/contact/account PII, no diagnostic value); the redactor now
  masks identifiers held as numbers and recurses lists; expanded the masked-key set
  (gigyaId/personId/iccid/imei/contractId/account/postcode/city/GPS); it now runs **once per
  restart** (not every poll) and the log line warns it may contain personal data — don't paste
  it publicly. The auto-discovered account id is no longer logged at INFO.
- **Reliability: exponential backoff** on consecutive poll failures (capped at 30 min) instead of
  a flat-interval re-auth loop; signal handlers are registered **before** the blocking startup
  work so shutdown is honoured during boot.
- **Correctness:** `detect_plug_suspect` now takes the decoded `PlugState` (was a raw int that
  would silently break on a library type change); fixed the success-log line that always printed
  `None` for plug status (`plug_status` → `charger_plug_status`).
- **Tests:** added a `poll_once` integration test (every sensor key is produced; graceful
  degradation when an endpoint fails) and redaction edge cases (numeric ids, secret-as-number,
  lists). 44 pass.

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
