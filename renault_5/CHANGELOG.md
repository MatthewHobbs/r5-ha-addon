# Changelog

## 1.0.1

- **Privacy: `debug_dump` redaction now covers more personal fields.** In addition to your
  VIN/account id/credentials/contact fields and GPS, the dump now masks vehicle **delivery
  and registration dates**, the **privacy-mode** setting, `vehicleId`/`batteryCode`, the
  stolen-vehicle-tracking flags, and the **`assets` block** (whose render URLs embedded the
  car's build-spec code). Added defense-in-depth masking of token/credential field names too.
- **Docs:** the `debug_dump` description now names what's redacted and that the whole dump
  should be treated as personal data — share privately, don't paste publicly. (Mirrors A290 v1.15.1.)

## 1.0.0

**Feature parity with the Alpine A290 add-on** — the R5 add-on graduates to 1.0.

- **Smart Charging is now first-class on both dashboards** (mirroring the A290). When you set
  the `charger_*` options:
  - **Bubble dashboard** — a **"Smart Charging" tab** in the main menu: smart/bump-charge as
    compact toggles, a charge-target **slider** with the live **%** and an **80%** recommended
    marker, a target-time **dropdown**, and an **off-peak badge** showing the current rate
    (green "Now: Off-peak" / red "Now: Peak rate") plus the cheap-window times in 24-hour. The
    **Location** button moves to a full-width row.
  - **Standard dashboard** — a styled **"SMART CHARGING" heading + Mushroom cards** matching
    the Climate/Charging Presets (no more clashing white inputs), with the off-peak badge.
  - New optional **`charger_dispatching`** option for the off-peak badge (an off-peak tariff
    `binary_sensor` is the best fit).
- Both dashboards are verified by the UI-test render gate; nothing changes unless the
  `charger_*` options are set.

## 0.16.0

- **New: climate (preconditioning) schedule sensors.** Two new sensors surface the car's
  programmed climate schedule — **Climate Schedule Mode** and **Climate Ready Time** (the days
  and times the cabin is set to be preconditioned, e.g. `Mon 07:00, Fri 08:30`). Together with
  the charge-schedule sensors this gives the full "ready to go by" picture. Read-only, shows
  *unavailable* when no schedule is set; nothing to configure.

## 0.15.0

- **New: scheduled-charging sensors.** Three new sensors surface the car's programmed charge
  schedule — **Charge Schedule Mode** (e.g. Scheduled vs Always), **Scheduled Charge Start**
  and **Scheduled Charge Duration** — read from the same settings the app already polls for
  preconditioning, so no extra API calls and nothing new to configure. They show *unavailable*
  on a car that doesn't expose a schedule.

## 0.14.0

- **Accurate Last Charge from Renault's own records.** When the car exposes its recent-charges
  history, the **Last Charge** sensors now use Renault's **authoritative per-session record**
  (start/end, SoC and energy recovered, duration, average power) instead of figures inferred
  from live battery polls. The live estimate stays as a fallback for a just-finished session
  the history hasn't posted yet — newest session wins, so the tiles populate immediately and
  settle on the official numbers once available. Automatic; no configuration needed.

## 0.13.0

- **Privacy: the car's GPS is rounded before publishing** to the retained MQTT topic —
  configurable `gps_precision` (default **4 dp ≈ 11 m**) instead of full precision; raise it
  for a more precise map pin, lower it for more privacy.
- **Internal (architecture): entity tables extracted into `catalog.py`** (out of `main.py`),
  matching the Alpine A290 add-on's structure — reduces lockstep drift. No behaviour change.
- **Internal: a contract test pins the renault-api fields the poller reads**, so a future
  library bump that renames a model field fails CI instead of silently breaking a sensor.
  (The R5 already auto-reconnected to MQTT; the A290 gained that in 1.8.0.)

## 0.12.0

- **New: optional "Smart Charging" card on the bundled dashboard.** If you control charging
  through a smart-charging integration (e.g. **Octopus Intelligent**, Ohme, Zappi, Wallbox),
  set the new `charger_smart_charge` / `charger_bump_charge` / `charger_target_soc` /
  `charger_target_time` options to your charger's entity ids and the deployed dashboard gains a
  **Smart Charging** card showing those controls next to the car's data. It's a built-in
  `entities` card (no extra HACS card needed), each blank option is skipped, and leaving them
  all blank (the default) adds nothing. Only affects the add-on's own bundled dashboards, not
  Topolino65's. See DOCS for the Octopus example.

## 0.11.1

- **More reliable dashboard auto-deploy.** The dashboard YAML is now **bundled in the add-on
  image and read locally**, instead of being fetched from `raw.githubusercontent.com` at
  deploy time — so `deploy_dashboard` no longer depends on GitHub being reachable when the
  add-on starts. (Images are still served from the jsDelivr CDN, unchanged.) This also brings
  the add-on's layout in line with the Alpine A290 add-on: the dashboards now live under
  `renault_5/dashboards/`. No change to entities, options, or the dashboards themselves.

## 0.11.0

UX pass — a fresh product + design review of the current dashboards, mirrored from the
Alpine A290 add-on (1.6.0) in lockstep.

- **`deploy_dashboard` stays off by default** (unlike the A290 add-on, which now defaults to
  `standard`). This add-on is a data layer for Topolino65's `renault-5-dashboard-view`, so it
  doesn't presume to auto-deploy its own bundled dashboard — set `deploy_dashboard` to
  `standard`/`bubble`/`both` if you want one. DOCS now make the Topolino65-vs-bundled choice
  explicit and explain where to find your VIN.
- **Last Charge tiles are now self-describing** (`Started / Ended / Duration`,
  `SoC Start / SoC End / SoC Gain`, `Energy Start / Energy End / Energy Added`) instead of
  repeating "Start/End/Gain" across all three rows.
- **Charge-limit (SoC) numbers stand out** — the value is now larger than its label, with the
  current SoC in white and the target in green.
- **Bubble dashboard: restored the "Stop Charging" signal** — the Commands pop-up now shows a
  disabled charge-control tile (parity with the standard dashboard) so the platform's
  charge-stop limitation is visible there too.
- **Fixes:** removed a duplicate "Initial" charging-power tile (same value as "Avg."); the
  "Run Test Charge" tile pointed at a non-existent entity (always "unavailable") — now wired
  to the real button.
- **`Drive Side` is hidden by default** (a RHD/LHD mapping artifact, not user-meaningful).
- **Status panel shows units** (range, kW, kWh, °C, distance per your locale).
- **Much lighter dashboards.** Backgrounds and the per-trim car renders converted to WebP and
  unused images removed — the bundled image assets dropped from ~8 MB to ~1 MB, so the
  dashboard paints far faster on mobile.

## 0.10.1

- **Fix: add-on failed to start after updating to 0.9.8 (AppArmor).** The custom AppArmor
  profile introduced earlier was too strict — it granted only execute (not read) on the
  s6-overlay boot chain, so the container's own `/init` script could not be opened
  (`can't open '/init': Permission denied`) and the add-on would not start after an update.
  The profile is now based on Home Assistant's reference add-on profile (broad `file`/
  `capability` access so the supervision tree and bashio always boot), while still denying
  the escalation primitives that carry real value: **no mount, no ptrace, no raw packet
  sockets**, and a constrained outbound network. The Supervisor security rating is unchanged
  (still **8/8**). CI now compiles the profile with `apparmor_parser` so a broken profile
  can't ship again. Thanks to @Dutchy-79 for the report (#12).

## 0.10.0

- **Read-only status panel in the sidebar (ingress)** (ported from the A290). The add-on now
  serves a small **at-a-glance panel** — battery, range, charging, plug, climate, charge
  limits, diagnostics — straight from the Home Assistant sidebar, no dashboard required. It is
  **read-only** and **auth-gated by Home Assistant** (ingress), served by the poller's own HTTP
  server, so it needs **no extra Supervisor permissions** and stores **no credentials or raw
  GPS**. *(This also takes the Supervisor security rating from 6 to 8.)* Mirrors
  **a290-ha-addon v1.5.0**.

## 0.9.8

- **Auto-deployed dashboards are now pinned to the release** (ported from the A290). When the
  add-on installs a dashboard it rewrites the fetch/CDN refs to this release's **`v<version>`
  git tag** (created by the release workflow) instead of `@main`, so a deployed dashboard is
  **reproducible** and can't shift under you when `main` moves. Dev/untagged builds still use
  `@main`. Mirrors **a290-ha-addon v1.4.8**.

## 0.9.7

- **Pre-built, signed images — faster, more reliable installs** (ported from the A290). The
  add-on is now published as a **multi-arch image** (`amd64` + `aarch64`) to **GHCR**, built
  and **Cosign-signed** (keyless OIDC) by a new `release` workflow on every version-bump merge
  to `main`. The Supervisor now **pulls** the image (via the new `image:` in `config.yaml`)
  instead of **building it on your device**. Each release also gets a **`v<version>` git tag +
  GitHub Release**. Mirrors **a290-ha-addon v1.4.7**.

## 0.9.6

- **Bubble dashboard — location parity** (ported from the A290). The **Vehicle Status** pop-up
  now shows the car **map** below its LOCATION text, and the **Location** pop-up gains the
  yellow **LOCATION** separator heading for parity with the other sections. Reuses the existing
  map/separator cards — no new dependencies. Re-deploy the bubble dashboard (or set
  `redeploy_dashboard: true` once) to pick it up. Mirrors **a290-ha-addon v1.4.6**.

## 0.9.5

- **Custom AppArmor profile — raises the Supervisor security rating to 6.** Ships
  `apparmor.txt`, confining the poller to the files (read-only system + `/app`, read-write
  `/data`) and network (outbound TLS/DNS/MQTT and the health-port bind) it actually needs —
  no mount, ptrace, raw sockets, or writes outside `/data`. (Rating 5 → 6.)
- Mirrors **a290-ha-addon v1.4.5**.

## 0.9.4

- **Guard `dashboard_url_path` against overwriting a built-in Home Assistant panel.** Before
  auto-deploying, the add-on now validates the configured path (lowercase slug, must contain
  a hyphen, and not a reserved HA path such as `energy` / `lovelace` / `developer-tools`) and
  **skips with a clear log line** instead of pushing a Lovelace config to it. Mirrors
  **a290-ha-addon v1.4.4**.

## 0.9.2

- **Privacy:** the account **password is added to the `debug_dump` redaction list** so it can
  never appear unmasked in a dump. (The account id was already logged only at `debug`.)
- **Fix the "Data Stale" dashboard pop-up** to point at the **add-on Log** and a restart
  instead of a non-existent `Auto-Reauth (8)` automation / `/config/renault_cli.log` — copy
  inherited from the old CLI-based predecessor and wrong for this add-on.
- **Bubble dashboard:** removed a redundant full-size background image from the **main menu**
  pop-up — it duplicated the page background and could render as a broken strip on first load.
- **Docs:** added a **"Before you start"** prerequisites section to `DOCS.md` so the required
  HACS frontend cards are installed **before** a dashboard is deployed.
- Mirrors **a290-ha-addon v1.4.2**.

## 0.9.1

- **Fix dashboard text truncation on phones, with consistent typography** (ported from the
  A290). Tile labels and section headers were cut off on narrow screens (especially 360px
  Samsungs). Both dashboards now **wrap that text on clean word breaks** instead of clipping,
  and the **font and sizes are now identical on every screen** — the responsive `@media` rules
  that changed fonts/sizes between phone and desktop have been removed. See the
  [mobile preview](docs/dashboards-on-mobile.md).
- **Automated responsive UI testing in CI.** New `ui-tests/` harness renders the bundled
  dashboards in a real Home Assistant (custom cards loaded) across the **top mobile device
  sizes** (iPhone 15 Pro Max/Pro/15/SE, Pixel 8/7a, Galaxy S24/S23/A54, + a 360px narrow
  bound) and **fails on any text truncation or broken card**, saving a screenshot per device
  as a CI artifact. Runs as the **UI Tests** workflow whenever the dashboards change.

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
  images by hand — see [`dashboards/CUSTOMISING.md`](dashboards/CUSTOMISING.md).

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
