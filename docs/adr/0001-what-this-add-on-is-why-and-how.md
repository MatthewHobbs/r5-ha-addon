# ADR 0001 — What this add-on is, why, and how to work on it

- **Repo:** r5-ha-addon
- **Status:** Accepted (2026-10-25), replacing the earlier records. Retrospective: the rows
  describe the tree at `6205a49`, with row 19 updated on 2026-10-26 for dashboard defaults and helpers.
  Rows 6, 10, 12 and 18 were updated on 2026-10-27 for runtime recovery.
  Rows 29 to 31 also record policy the code cannot state: that a
  release is cut when I ask, which checks are required, and that r5 follows a290. **Working in
  this repository** carries the operating context, so this file alone is enough to start.
- **Context:** this repository is a Home Assistant add-on for the Renault 5 E-Tech. It polls the
  Renault Kamereon API for one car and publishes what it finds to Home Assistant over MQTT. Others
  install it, so what it does with a credential, a location or a failure matters more than how much
  it exposes. It is the port of `a290-ha-addon`, the Alpine A290 add-on, and the two share one
  platform; a290 leads and this repository follows. It continues Topolino65's
  `renault-5-dashboard-view`, whose dashboards and design it keeps, and replaces that project's
  `venv`, command-line client and shell-script data layer.
- **North star:** the add-on tells the truth about the car, or says it does not know. It never
  invents a fresh reading, never ships a control the car refuses, never lets a secret or a precise
  location leave the machine unmasked, and never lets a release out that was not booted first.

## Decision

One add-on, one car, one poll loop, published as discovered MQTT entities, with every claim about
the car gated on what the car says it supports and every failure shown as a failure. The runtime is
small and its checks are many, because it runs unattended in other people's homes.

The table is the record, and it is kept current rather than appended to: a row says what is true
now. When a decision reverses, the row is rewritten and re-dated, and the change is explained in
the changelog like any other.

| # | Decision | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | The add-on is an MQTT publisher, not an integration. It polls on an asyncio loop, publishes retained state to one topic, and registers its entities by Home Assistant discovery. The broker comes from the Mosquitto add-on through the Supervisor | r5 | Done | `renault_mqtt.runtime` (`run_main`); `renault_5/app/main.py` (`main`, `poll_once`); `renault_5/config.yaml` (`services`); `renault_5/run.sh` |
| 2 | Credentials are entered once, on the Configuration page, and nowhere else. Options become `R5_*` environment variables at start; a missing username, password, VIN or broker stops the add-on with a message that names the setting | r5 | Done | `renault_5/config.yaml` (`options`, `schema`, `password`); `renault_5/run.sh`; `renault_mqtt.runtime` (`run_main`); `renault_5/app/main.py` (`main`) |
| 3 | Entities are declared as tables, per model. Names, units, device classes, icons, optional endpoints, controls and charge-limit sliders live in `catalog.py`; the object-id prefix and the environment prefix are defined there and nowhere else, so the a290 port changes the catalogue and not the loop. Object ids keep Topolino65's naming without his `_api` and `_mi` suffixes | r5 | Done | `renault_5/app/catalog.py` (`OBJ_PREFIX`, `ENV_PREFIX`, `NODE`, `DEVICE`, `SENSORS`, `BINARY_SENSORS`, `NUMBERS`, `ACTION_BUTTONS`) |
| 4 | The poll and publish plumbing is a shared core, pinned to a commit. `renault_mqtt` (the MQTT seam, charge-session reconciliation, parsing, redaction, debug dump) is installed from `MatthewHobbs/renault-mqtt` at `CORE_REF` and not vendored; the add-on injects its prefix and catalogue into it | r5 | Done | `renault_5/Dockerfile` (`CORE_REF`, the `renault-mqtt @ git+…` install); `renault_5/app/main.py` (`config.ENV_PREFIX = ENV_PREFIX`, `mqtt.configure(catalog)`) |
| 5 | The add-on speaks to Kamereon itself, through `renault-api`, and sends every control natively. Home Assistant's `renault` integration is not needed. Start Charging, Flash Lights, Sound Horn, Start and Stop Air Conditioner and Refresh Location are the six the R5 supports; charge mode, charge stop and tyre pressure are not in its endpoint map and are not published | r5 | Done | `renault_5/app/main.py` (`COMMAND_ACTIONS`, `run_command`); `renault_5/app/catalog.py` (`ACTION_BUTTONS`, `OPTIONAL_ENDPOINTS`); `renault_5/tests/test_api_contract.py` (`R5E1VE_MAP`) |
| 6 | Capability checks distinguish unknown from unsupported. Core resolves pending probes incrementally; the app owns endpoints, defaults, acquisition, invalidation and logging. Failed probes alone are retried after successful polls, within a timeout, updating discovery and reconnect/panel support. Definite false is not retried and errors preserve prior positives. Unknown data defaults to supported, except `hvac-settings`; unknown controls stay hidden | r5 | Done | `renault_mqtt.support` (`resolve_support`); `renault_5/app/main.py` (`detect_supported`, `_supports`, `SUPPORT_ENDPOINTS`, `main`); `renault_5/app/catalog.py` (`OPTIONAL_ENDPOINTS`, `PESSIMISTIC_ENDPOINTS`, `SOC_ENDPOINT`) |
| 7 | `hvac-settings` is called only when the car lists it, and a failing call is paused rather than retried every poll. Support is the first gate and a pessimistic one (row 6); the breaker is the second. Core owns the advertisement gate, trip counter, retry cadence and recovery reset; the app owns thresholds, breaker state and bounded logs. After three consecutive failures it is switched off, probed again every twelve polls, and re-enabled if it answers | r5 | Done | `renault_mqtt.hvac` (`hvac_due`, `core.poll_hvac_settings`); `renault_mqtt.poll` (`poll_vehicle`); `renault_5/app/main.py` (`BREAKER_TRIP`, `BREAKER_RETRY_EVERY`, `_hvac_due`, `poll_once`); `renault_5/app/catalog.py` (`DATA_GATED_SENSORS`) |
| 8 | Two freshness questions are two entities. `data_stale` asks whether the car has reported within `stale_hours`, from the battery-status timestamp; `poll_failing` asks whether this add-on can reach Kamereon. A successful poll never asserts that the car's data is fresh. A parked car legitimately reads stale and not failing | r5 | Done | `renault_5/app/main.py` (`freshness_fields` imported from `renault_mqtt.freshness`, the success and failure paths of `main`); `renault_5/app/catalog.py` (`r5_data_stale`, `r5_poll_failing`, `r5_battery_last_activity`) |
| 9 | An unknown value stays unknown. The car's own report time is published as it came, never replaced by ours. A location the API marks unusable (a sentinel or out-of-range coordinates) is rejected and the last good fix time is carried forward, so the stale-location guard stays armed. A key with no value is omitted and shows as `unknown`, not as a made-up `off` | r5 | Done | `renault_mqtt.poll` (`poll_vehicle`); `renault_5/app/main.py` (`poll_once` and `battery_last_activity`, `freshness_fields`) |
| 10 | Controls are conservative. Core owns per-command debounce, in-flight cancellation cleanup, climate/charge-limit lock separation, preconditioning temperature fallback and SoC slider validation; the app supplies short-lived bounded sessions, login, command actions and slider/role tables. Climate start/stop share a lock through login and action; charge-limit writes use a separate lock, reading the opposing slider first. Other controls remain independent. Cancellation releases the lock; only a dispatched command keeps its debounce stamp. Slider range and step are enforced on commands too | r5 | Done | `renault_mqtt.commands` (`COMMAND_DEBOUNCE_S`, `command_lock_get`, `dispatch_command`, `hvac_start_action`, `precondition_temp`, `set_soc_level`); `renault_5/app/main.py` (`run_command`, `COMMAND_DEBOUNCE_S`, `set_soc_level`, `_command_lock_get`, `_hvac_start_action`, `_precondition_temp`, `NUMBER_RANGES`); `renault_5/app/catalog.py` (`NUMBERS`: minimum 15 to 45, target 55 to 100, step 5) |
| 11 | A control that can hurt is off until asked for. Refresh Location is withheld unless `enable_refresh_location` is set, because on a parked car it can replace the recorded position with no fix. The command is refused as well as the button hidden, since the entity can be pressed from voice, automations and any dashboard | r5 | Done | `renault_5/config.yaml` (`enable_refresh_location: false`); `renault_5/app/main.py` (`LOCATION_CMDS`, `run_command`) |
| 12 | The add-on logs in once and keeps the session. Core owns the single-poll-owner lifecycle, account resolution, auth-failure classification and backoff loop; the app supplies transport, login, locale, exception types and logging. A rejected or expired login, recognised by exception type, drops it immediately and turns on API Auth Failure; other errors drop it every third failure. Each API request has a 60 second ceiling; polls are bounded under the interval. Failures back off exponentially to the larger of the interval and thirty minutes, never polling faster than a healthy car | r5 | Done | `renault_mqtt.session` (`VehicleSession`); `renault_mqtt.runtime` (`resolve_account`, `core.is_auth_failure`, `run_main`); `renault_5/app/main.py` (`VehicleSession`, `_login_vehicle`, `resolve_account`, `API_TIMEOUT`, `_AUTH_EXCEPTIONS`, `main`); `renault_5/app/catalog.py` (`r5_api_auth_failure`) |
| 13 | A secret never reaches a log. A redaction filter sits on every handler; the `renault_api` loggers are held at INFO whatever `log_level` says, because at DEBUG the library logs whole request and response bodies outside that filter; `debug_dump` is the one route for decoded responses and goes through redaction; dashboard-deploy errors are masked as well. The account id found by VIN lookup is registered so it is masked too | r5 | Done | `renault_mqtt.runtime` (`resolve_account`); `renault_5/app/main.py` (`setup_logging`, `resolve_account`); `renault_5/app/deploy.py` (`_redact`); `renault_mqtt.config` (`_RedactingFilter`, `redact`), reached through `main.py` |
| 14 | Location is opt-out and coarsened. `publish_location` can disable it; the position is rounded to `gps_precision` places (default 4, about 11 m) before it reaches the retained topic; the tracker carries attributes and no state topic, so Home Assistant resolves home and zones from the coordinates. The status panel's snapshot holds no raw coordinates and no credential | r5 | Done | `renault_5/config.yaml` (`publish_location`, `gps_precision`); `renault_mqtt.runtime` (`run_main`); `renault_5/app/main.py` (`GPS_PRECISION`, `_LATEST`, `main`) |
| 15 | State that must survive a restart is a small file, written atomically. The plug baseline, charge-session tracking, the last good GPS time and the last car timestamp live in `/data/state.json`, saved after every poll; a file of the wrong shape is read as empty and a failed write is a warning, not a crash. Core owns plug baseline/evidence logic; the app owns Renault enum interpretation, clock, thresholds and persistence | r5 | Done | `renault_mqtt.plug` (`detect_plug_suspect`); `renault_5/app/main.py` (`STATE_FILE`, `load_state`, `save_state`, `detect_plug_suspect`) |
| 16 | The car's platform quirks are handled where they arise. The configured battery capacity (52 or 40 kWh) is used because the API reports 0; the float charge state is decoded through the library enum into fixed labels; distance units follow the locale (miles only for `en_GB`); left and right seats are mapped to driver and passenger by drive side; a plug state that contradicts the odometer and the state of charge is flagged | r5 | Done | `renault_5/app/main.py` (`CHARGE_STATUS_LABELS`, `PLUG_STATUS_LABELS`, `RHD_LOCALES`, `MILES_LOCALES`, `detect_plug_suspect`); `renault_5/config.yaml` (`battery_capacity_kwh`) |
| 17 | An entity that is retired is cleaned up, and one that adds noise is hidden. Discovery clears `RETIRED_SENSORS` so upgraded installs lose them; `drive_side` is published disabled by default | r5 | Done | `renault_5/app/catalog.py` (`RETIRED_SENSORS`, `DEFAULT_DISABLED_SENSORS`) |
| 18 | `/healthz` backs the image `HEALTHCHECK`; a read-only ingress panel and retained MQTT share the same vehicle snapshot. Failed polls keep this process's last good readings and original timestamps with updated diagnostics; recovery replaces them. No readings are invented before success. The event loop serves both health and panel, so a deadlock answers neither; a loop that stops completing answers 503 once its expected-progress deadline passes, and a failing poll that completes stays 200 | r5 | Done | `renault_mqtt.runtime` (`HEALTH_PORT`, `panel_page`, `panel_state`, `start_health_server`, `run_main`); `renault_5/app/main.py` (`main`, `start_health_server`, `_panel_page`, `_panel_state`, `_LATEST`); `renault_5/Dockerfile` (`HEALTHCHECK`); `renault_5/config.yaml` (`ingress`); `renault_5/app/panel.html` |
| 19 | New installs deploy the standard dashboard by default (`deploy_dashboard: standard`), matching a290; an existing user's `none` remains `none`. Users can select Bubble, both or none. The add-on writes through the Supervisor's Core WebSocket, creates once and leaves existing dashboards alone unless `redeploy_dashboard` is set. It validates URL paths, removes a newly created dashboard if saving fails, and cannot stop the poller. Layout YAML is bundled in the image; images come from jsDelivr pinned to the release tag, and `car_render` picks one of 22 R5 renders. Optional test helpers (`r5_test_*`) and the pretty-location template ship for manual installation, never auto-deploy | r5 | Done (updated 2026-10-26) | `renault_5/app/deploy.py` (`run_deploy`, `_deploy_one`, `_validate_url_path`, `RESERVED_URL_PATHS`, `CDN`, `REF`, `_selected_render`); `renault_5/config.yaml` (`deploy_dashboard`, `dashboard_url_path`, `redeploy_dashboard`, `car_render`, `homeassistant_api`); `renault_5/Dockerfile` (`COPY dashboards/*.txt`); `renault_5/dashboards/Packages/r5_test.yaml`; `renault_5/dashboards/Templates/r5_pretty_location.yaml`; `scripts/supervisor-pilot.sh` (`cmd_install`, `dashboard_targets`) |
| 20 | Smart charging is mapped, not reimplemented. Five optional `charger_*` options name existing Home Assistant entities from any charger integration; a blank slot is skipped, and with all five blank no card is added | r5 | Done | `renault_5/app/deploy.py` (`_CHARGER_ENTITIES`, `_charger_cards`, `_charger_popup`, `_inject_bubble_charging`); `renault_5/config.yaml` (`charger_smart_charge`) |
| 21 | The image is small, pinned and confined. The base is the Home Assistant base image by digest; Python dependencies are hash-locked and installed with `--require-hashes`; the core is installed by commit with `--no-deps`, and its build toolchain is removed in the same layer; `tempio` is removed because scanners flag it and nothing uses it; the AppArmor profile pins its ABI, allows TCP and UDP and denies mount, umount, pivot_root and ptrace. It ships prebuilt for `amd64` and `aarch64` with a keyless provenance attestation | r5 | Done | `renault_5/Dockerfile`; `renault_5/app/requirements.in`; `renault_5/app/requirements.txt`; `renault_5/apparmor.txt`; `.github/workflows/release.yaml` (`manifest`); `renault_5/config.yaml` (`image`, `arch`) |
| 22 | The Renault library is ranged and its contract is tested. `renault-api` is `>=0.6.1,<0.7` in the source, exactly locked in `requirements.txt`, and a test fails unless the lock satisfies the source. It was pinned only while the releases were wrong about this car; fixed upstream, it takes an ordinary Dependabot PR. Another test loads representative Kamereon payloads through the library's own schemas and asserts every field and helper the poller reads, and a third fails on any change to the R5's endpoint map | r5 | Done | `renault_5/app/requirements.in`; `renault_5/tests/test_api_contract.py` (`test_requirements_in_specifiers_are_satisfied_by_the_lock`, `test_r5_endpoint_map_is_the_reviewed_one`) |
| 23 | Dependabot is the only dependency bot, and it covers the runtime Python, the Actions and the Dockerfile's images. What no manifest describes (the core commit, the UI gate's Home Assistant image and card versions, the pilot's image digests, and tool versions quoted inside workflow steps) is bumped by hand | r5 | Done | `.github/dependabot.yml` (`pip`, `github-actions` and `docker` ecosystems) |
| 24 | The Renault library gets its own pull request. It is the one dependency whose bump changes what the car exposes, so it is grouped alone and reviewed against the endpoint-map test | r5 | Done | `.github/dependabot.yml` (the `renault-api` group); `renault_5/tests/test_api_contract.py` |
| 25 | Every pull request runs the same gates locally and remotely. Lint (ruff, yamllint, hadolint, shellcheck, the add-on linter, an AppArmor compile), the suite with coverage reported but no percentage threshold, Bandit, pip-audit, Trivy, a check that refuses committed VINs, coordinates, personal email addresses and home paths, a check that the declared minimum Home Assistant is a real release no newer than the allowed ceiling, and an amd64 build. `just ci` runs the local subset | r5 | Done (updated 2026-10-26) | `.github/workflows/ci.yaml`; `justfile`; `scripts/pii_check.py`; `scripts/ha_minimum_check.py` |
| 26 | Documentation is held to the code by a check. A change to the options or the published entities fails unless the docs change with it; a change to the version fails unless it is exactly a release; changelog history is append-above only | r5 | Done | `scripts/docs_sync_check.py`; the `Docs sync` job in `.github/workflows/ci.yaml` |
| 27 | The dashboards are rendered in a real Home Assistant on seven phone sizes, and a truncated label or an error card fails the run. It has two legs, current stable and the declared minimum read from `config.yaml`; its inputs are pinned and bumped together; it always reports so it can be required | r5 | Done | `ui-tests/run.sh`; `ui-tests/seed.py`; `ui-tests/check_overflow.py`; `ui-tests/devices.json`; `.github/workflows/ui-tests.yaml` |
| 28 | The add-on is installed under a real stable Supervisor before it ships. A pilot installs it on both architectures at current stable Core and at the declared minimum, and checks the versions, the image's provenance, `/healthz`, the retained discovery and availability topics, that Renault was tried and refused, the deployed dashboard in Core, and AppArmor enforcement. The release run repeats it on the published per-arch image before the tag | r5 | Done | `scripts/supervisor-pilot.sh`; `.github/workflows/supervisor.yml`; the `pilot` job in `.github/workflows/release.yaml` |
| 29 | A release is requested, then published from a tag I sign. Ordinary changes add a changelog entry under `## Unreleased` and leave `version` alone; a release PR changes only `version` and that heading, and publishes nothing. `just tag <version>` refuses unless the version is above every existing tag, the merge commit is on main and every workflow on that exact commit passed; it then signs `v<version>`. That run builds both arches, scans each image by digest (HIGH/CRITICAL fails the release), boots them under a stable Supervisor, waits for CI and both render legs, attests the staged manifest, and only then publishes the version tag and the GitHub Release | r5 | Done | `scripts/prepare_release.py`; `scripts/check-version-order.sh`; `.github/workflows/release.yaml` (`prepare`, `build`, `manifest`, `pilot`, `validated`, `release`); `justfile` (`release`, `tag`) |
| 30 | `main` takes only signed, linear, squashed history through twelve required checks, and the rules bind administrators: Lint, Tests, Security, Build, Docs sync, Comment length, both UI legs and the four pilot legs. Parity is not one of them | r5 | Done | Branch protection on `main`, read with `gh api` on 2026-10-26: `api.strict`, `api.enforce_admins`, `api.required_signatures` and `api.required_linear_history` all on |
| 31 | The twin is derived and checked. This repository is held to `a290-ha-addon` by a script that rewrites its names onto a290's, diffs the trees, and fails on any difference not listed with a category, an exact count and a reason. a290 leads; r5 mirrors its changes and does not release ahead of it | r5 | Done | `scripts/parity_check.py`; `scripts/parity/map.tsv`; `scripts/parity/expected.tsv`; the `parity` job in `.github/workflows/ci.yaml` |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Context

The R5 E-Tech is one of two Renault-group cars reachable through the same Kamereon API, and the
library that talks to it keeps a hard-coded map of which endpoints each model exposes. That map,
not the library's documentation, decides what this car can do, which is why rows 6, 7 and 22 treat
support as something to probe, observe and pin and not something to assume. The platform also
reports a battery capacity of 0, a charge state that is a float, charging power in unreliable
units, and a location that is sometimes a sentinel and not a position. Most of the rows above exist
because of one of those facts.

The add-on runs as a container it does not control, holds a credential to a car and, if allowed,
that car's location, and lives in a public repository. That is why rows 13, 14, 21 and 25 are as
strict as they are.

## Working in this repository

Enough to start without reading anything else. Where this section and the code disagree, the
code is right and this section is the bug.

**Where things are.** `renault_5/app/main.py` is the poll loop, MQTT discovery, the controls and
the health server; `catalog.py` holds the entity tables, endpoint constants and the retired lists;
`deploy.py` writes the dashboards; `core/config.py` is the pinned core's own. Tests sit in
`renault_5/tests/`, the dashboards and their assets in `renault_5/dashboards/` (Topolino65's,
kept), the render gate in `ui-tests/`, the pilot in `scripts/supervisor-pilot.sh`.
`renault_5/config.yaml` is the manifest the Supervisor reads.

**Checks.** `just` is the entry point, and its recipes mirror the CI steps where they can. `just
venv` builds the virtualenv the test recipe uses — a stale one fails MQTT tests that look like
code faults. `just ci` runs ruff, the decision-record citations, the PII gate, the HA-floor bound,
both self-test suites and the tests with coverage reported but no percentage threshold. What it
cannot run is remote only: Hadolint, the app linter, the AppArmor compile, Bandit, pip-audit,
Trivy, the amd64 build, the render gate on seven phone sizes at two Core versions, and the
Supervisor pilot on four legs. A
green `just ci` is therefore not a green PR: run `ui-tests/run.sh` for a dashboard change, and
treat a runtime change as needing the pilot.

**Dependencies.** Everything is pinned, Python with hashes, and a test fails if
`requirements.in` and the lock disagree. `renault-api` is the one dependency that changes behaviour
rather than hygiene: the per-model endpoint map inside it decides what this car exposes, so a bump
is reviewed against `tests/test_api_contract.py`, never waved through. Dependabot is the only bot.
The core (`renault-mqtt`) is a commit, `CORE_REF`, in `renault_5/Dockerfile`; nothing bumps it
automatically, and a fix there does not arrive until that line moves.

**The pair.** `a290-ha-addon` leads and this repository follows: a change that is not per-model
lands there first, then here, and it never releases ahead of a290. `scripts/parity_check.py`
rewrites this repo's names onto a290's and fails three ways: an unlisted difference, a listed one
that has gone away, and a count that no longer matches. Never change a count without reading the
lines the run printed. `parity_check.py`, `pii_check.py`, `ha_minimum_check.py` and
`adr_evidence_check.py` are byte-identical to a290's, so editing one fails the twin's job until
both copies agree; `docs_sync_check.py` is not yet, and `expected.tsv` lists that as pending.
`map.tsv`'s `mask` kind hides a version pin on both sides so a bump does not read as drift, and a
mask that stops matching is the signal a pin has moved.

**Releasing.** A change adds its entry under `## Unreleased` and leaves `version` alone. On
request, `just release <version>` turns that heading into the version and moves the manifest; that
PR is merged, then `just tag <version>` signs `v<version>` on the merge commit. The tag run does the
rest: it checks the tag, pushes the images, boots them under a stable Supervisor, waits for CI and
the render gate, and only then publishes the manifest and the Release. Tags are immutable and
signing is on by default, so `git tag` needs `-m` or it hangs, and a tag pushed early burns the
version.

**Traps, in the order they have cost time.** Home Assistant derives an entity's id from the device
and friendly names, not from the discovery `object_id`, so tests and dashboards must derive ids the
same way. `debug_dump` is the only route to decoded API responses and it goes through redaction;
never add a logging path that bypasses `_debug_redact` or lifts the `renault_api` clamp, because
the library logs whole bodies at DEBUG. Dashboard text wraps, it never shrinks. The injected style
still covers `.content p` only, so a heading Home Assistant 2026.10 draws as an `h2` can clip on a
360 px phone until a290's fix is ported. An absent option is not a default: state it. The PII gate
pairs a latitude with a longitude across lines, which comments may separate but blank lines may
not. The docs-sync and record checks are diff-based or citation-based, so they catch what they are
pointed at and nothing else.

**Where the local state lives.** `HANDOVER.md`, `AGENTS.md` and `CLAUDE.md` are git-excluded, so
they are not here: the queue, the operator's notes and the long reference respectively.
`docs/adr/` holds this record and its index. The Supervisor reads this repo's files straight from
`main`, so a merge that changes `config.yaml`, `DOCS.md`, `apparmor.txt` or the changelog reaches
users while they still run the previous image.

## Consequences

- The runtime stays small on purpose. A new reading is a catalogue row and a poll line; a new
  control is a catalogue row and a gated command. Anything that needs a decision about the car's
  behaviour belongs here as a row first.
- The shared core is a pinned commit of another repository. A fix there does not reach this add-on
  until `CORE_REF` moves, and nothing bumps it automatically (row 23).
- r5 differs from a290 where the cars do. It still publishes Cabin Temperature; whether an R5
  supplies it remains unestablished. Production entity IDs, command topics and status-panel fields
  retain their current names; this helper migration does not rename them. The test package is
  namespaced `r5_test_*` and both repos now default to standard dashboard deployment.
- Start Charging is published, and is documented as possibly doing nothing under scheduling
  outside the car. `set_charge_start()` works by switching off the car's own charge programs. On
  an A290 on Octopus Intelligent that left the car waiting to charge. For the R5 the same outcome
  is inferred from the identical library path and has not been observed.
- Everything in the table is checked by something that can fail, except row 30, settings that no
  job reads back.
- Row 24 was an Open defect until this record: `pip` and `docker` pointed at `/alpine_a290`, which
  this repository does not have, so neither ecosystem was updating. The paths now point here.
- **Not established:** how this add-on behaves on a real R5 for the endpoints I have not seen
  refuse or accept, beyond what the tests and the changelog record; whether an R5 returns cabin
  temperature outside HVAC activity; that the Supervisor on a user's host acts on the image
  `HEALTHCHECK`; and any behaviour of the shared core that the add-on's own tests do not exercise.
