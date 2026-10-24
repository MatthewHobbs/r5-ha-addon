# Contributing

Home Assistant **add-on** for the **Renault 5 E-Tech**. It polls the Renault/Kamereon API
(`renault-api`) on an asyncio loop and publishes `sensor.r5_*` / `binary_sensor.*` /
`button.*` / `number.*` entities over **MQTT auto-discovery**. There are no shell scripts, no
`venv` and no `secrets.yaml`. Credentials are entered on the add-on's Configuration page. It
continues [`Topolino65/renault-5-dashboard-view`](https://github.com/Topolino65/renault-5-dashboard-view)
(full credit for the original dashboards, assets and design), replacing that project's `venv` +
`renault-api` CLI + shell-script data layer.

Others install this add-on, so a runtime change gets the container boot below before it merges.

A sibling repo, **`MatthewHobbs/a290-ha-addon`**, is the Alpine A290 add-on this is **ported
from** (the R5 E-Tech and A290 share the CMF-BEV / KCM platform). **Keep the two in lockstep.**
Most feature and fix work here should be mirrored there (adjusting for per-model API
differences), and the other way round. a290 leads: r5's changes mirror a290's and never merge
ahead of them.

**The parity check enforces that** (a290's ADR 0001; this repo's decisions live in `docs/adr/`).
`scripts/parity_check.py` rewrites r5's names onto a290's (`scripts/parity/map.tsv`), diffs the
trees, and fails on any difference that `scripts/parity/expected.tsv` does not list with a
category, an exact count and a reason, and on any entry whose difference is gone. Run
`just parity` (clones a290's main, or `PARITY_TWIN=../a290-ha-addon just parity` to use a git
checkout). CI runs it as `Parity with a290`. When your change creates a difference, port it or
add a `pending` entry naming its Target, beside the existing entries for that path. When you fix
one, delete its entry. Never raise a count without reading the lines it printed. The script is
shared verbatim with a290; the map and list are each repo's own copy.

## Layout

```
renault_5/                   the add-on (this is what HA installs)
  app/
    main.py                  asyncio poller, MQTT discovery, controls, charge-limit numbers,
                             debug_dump, health endpoint (/healthz). State (plug
                             stuck-detection, charge-session tracking, health) persists to
                             /data/state.json.
    catalog.py               entity tables: SENSORS / BINARY_SENSORS / ICONS / NUMBERS /
                             ACTION_BUTTONS / OPTIONAL_ENDPOINTS / RETIRED_SENSORS /
                             DEFAULT_DISABLED_SENSORS / SOC_ENDPOINT
    deploy.py                optional dashboard auto-deploy via the HA core API
    panel.html               the read-only ingress status panel
    requirements.in/.txt     pinned deps (see "Dependencies")
  tests/                     pytest: runtime, main, MQTT, deploy, dashboard conformance and the
                             renault-api contract
  config.yaml                add-on manifest: version, options + schema
  Dockerfile                 pinned base (FROM ghcr.io/home-assistant/base:<tag>@sha256), HEALTHCHECK,
                             root user
  run.sh                     bashio entrypoint (options via bashio::config)
  apparmor.txt               the add-on's AppArmor profile
  DOCS.md / CHANGELOG.md     the add-on's HA docs page + changelog
  dashboards/                front-end.txt (standard) + front-end-bubble.txt (Bubble Card) +
                             Images/, CUSTOMISING.md. Bundled into the image (COPYed in the
                             Dockerfile); deploy.py reads the *.txt locally, images are served
                             from the jsDelivr CDN
ui-tests/                    containerized HA + Playwright responsive/overflow gate
docs/                        dashboards-on-mobile.md + screenshots (user docs), adr/
reference/                   local-only Topolino65 upstream checkout, sourced for assets;
                             gitignored, never committed
ruff.toml / repository.yaml / README.md / LICENSE
```

## Dependencies

`renault_5/app/requirements.in` (the source) and `requirements.txt` (hash-locked) pin the Python
dependencies, `renault-api`, `paho-mqtt` and `PyYAML`, at the versions in those two files. Each
is pinned in BOTH. Bump both, or the next regeneration silently reverts the one you missed;
`tests/test_api_contract.py` fails when they disagree.

**The shared core.** Most of the runtime (MQTT, discovery, parsing, redaction, the debug dump)
lives in `renault-mqtt`, shared with the a290 add-on. The Dockerfile pins it by full commit SHA
(`ARG CORE_REF`, with the release tag in the comment above it) and installs it with
`--no-build-isolation`. Renovate proposes a new core release as its own PR (the "renault-mqtt
core" group); bump it like any runtime change, with the container boot.

**CI tools.** The workflows and the justfile pin pytest, pytest-cov, bandit, pip-audit, playwright,
ruff and Trivy's version. Renovate proposes them together in a "CI tools" PR (a major release
comes separately), at least 3 days after a release, Trivy 7. Only those five Python tools are
tracked: workflow steps that need runtime packages install the hash-locked `requirements.txt`,
never a hand pin, so runtime Python stays Dependabot's alone. A new Python tool in a workflow is
ignored until its name is added to the allowlist in `.github/renovate.json` (`matchPackageNames`).
ruff's version lives in `ruff.toml` (`required-version`), which `just lint` runs and Renovate bumps
together with the action's pin. The workflows' Python stays on 3.14, the add-on runtime's; when
the base image moves, move `python-version` and the `allowedVersions` bound in the same change.

**`renault-api` follows upstream.** Dependabot proposes it weekly in a group of its own, so each
bump is its own PR, and r5's merges after a290's. Review each bump against
`renault_api/kamereon/models.py` `_VEHICLE_ENDPOINTS` (the R5 is model `R5E1VE`, the A290
`A5E1AE`), which `tests/test_api_contract.py` pins, check what the new release logs at INFO and
above (see "Secrets never get logged"), and boot the container. That map, not the readthedocs
pages, is the authoritative source for what each car exposes.

What is known about the R5 endpoints:

- The R5 supports **all six native controls**: charge-start, flash lights, sound horn, HVAC
  start/stop, and refresh location. Charge-mode, charge-stop and tyre-pressure are `None` in its
  map and are not published.
- The add-on probes `supports_endpoint()` at startup and only publishes what is available.
- **Charge-start is not an instant-charge command.** Both `R5E1VE` and `A5E1AE` map it to KCM
  `charge-start-via-settings`, and `set_charge_start()` works by switching off the car's **own**
  charge programs in `ev/settings`. On an A290 on Octopus Intelligent that was a no-op: the car
  stayed "Waiting to Charge" (a290-ha-addon #95). For the R5 that outcome is **inferred** from the
  identical library path, not observed on an R5.
- **Platform caveats (CMF-BEV, KCM):** `batteryCapacity` is always 0 (the add-on uses the
  configured capacity); `chargingStatus` is a float `ChargeState` (decoded via the library enum,
  not a plain 0/1/-1); `chargingInstantaneousPower` units are unreliable; `batteryTemperature` is
  sometimes absent.

## Local checks: run the full suite before pushing

CI (`.github/workflows/ci.yaml`) runs **Lint, Docs sync, Parity with a290, Tests, Security and
Build (amd64 sanity)**. Run them locally before pushing, not just ruff and pytest. macOS and Linux
behave differently (the UI gate has caught Linux-only font truncations a local macOS run missed),
so a green local partial run is not a green CI. `just ci` runs lint (ruff, yamllint, shellcheck),
the PII and HA-minimum checks, the parity and docs-sync self-tests and the tests; hadolint, the HA
add-on linter, Bandit, pip-audit, Trivy, the full parity comparison (`just parity`) and the image
build run only in CI, or by hand with the commands below.

```sh
# lint
ruff check renault_5/app
yamllint -c .yamllint renault_5 repository.yaml
hadolint -c .hadolint.yaml renault_5/Dockerfile
shellcheck renault_5/run.sh

# test (coverage gate is 95%)
python3 -m pytest renault_5/tests -q --cov=renault_5/app --cov-report=term-missing --cov-fail-under=95

# security
bandit -r renault_5/app -ll
pip-audit -r renault_5/app/requirements.txt
trivy fs --scanners vuln,misconfig,secret --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed .
```

`scripts/pii_check.py` fails on personal data in any tracked file (coordinates, VINs, emails,
home-directory paths). Keep test data synthetic. It is shared verbatim with a290.

## UI gate

The `ui-tests/` gate (its own `ui-tests.yaml` workflow) is run with `ui-tests/run.sh`. The workflow
runs on every PR and decides in-job whether to render: a change to the dashboards, `deploy.py`,
`catalog.py`, `config.yaml`, `ui-tests/` or the workflow itself renders; anything else reports
success with the render skipped. It boots a throwaway HA container, seeds entities, and
uses Playwright across seven phone viewports to fail on any text truncation or
`hui-error-card`. Run it whenever you touch `renault_5/dashboards/` or `ui-tests/`, with
`UI_TESTS_DARK=1` to match CI. A light-theme run (no `UI_TESTS_DARK`) is the one that shows
contrast problems; the truncation check cannot see colour.

The harness **pins its render inputs** in `ui-tests/run.sh` so the gate is deterministic: the HA
image and the mushroom, button-card, card-mod and Bubble Card versions. A floating
`:stable`/`@latest`/`@master` previously drifted under the gate and made it flake (marginal
sub-pixel truncations on one high-DPR device that vanished on the next run). Hand-bumped pins
went stale instead (2026.7.1 while users ran 2026.9.x), so **Renovate tracks all five** and
proposes them as **one grouped PR** ("UI gate render inputs", stable HA releases only). The gate
on that PR confirms the combination before it is pinned. Keep them identical to the a290 twin's
`ui-tests/run.sh` and Renovate rules (lockstep).

The gate renders against that stable pin and against the declared minimum (`homeassistant:` in
`renault_5/config.yaml`, currently 2026.8.1), read from the manifest so the test cannot drift
from the claim. The stable leg warns (does not fail) while its pin lags current stable; the fix is
merging Renovate's render-inputs PR. `scripts/ha_minimum_check.py` (CI Lint job, `just ha-min`) fails if the minimum is
missing or newer than the `.1` of the month before current stable, read live from
`version.home-assistant.io/stable.json`, or is not a published release on PyPI (exact spelling,
no pre-releases). An unreachable PyPI fails the step too. The script is shared verbatim with
a290, so change it in both repos together.

Ruff config (`ruff.toml`): line-length 120, target py314, `select = E,F,W,B,I`,
`ignore = E501,B008`.

## Before merging a runtime change: build and boot the container locally

This add-on's image is pulled by tag (`config.yaml` `version`), so build and boot it locally and
observe the changed behaviour before merging any runtime PR.

Two things make a naive run mislead or fail:

- **`bashio::config` reads the Supervisor API, not `/data/options.json`.** Mounting a stub
  options file achieves nothing: `/run.sh` gets empty config and the add-on correctly exits
  with `Missing required setting`. Bypass `run.sh` with `--entrypoint python3` and pass the
  `R5_*` env vars directly.
- **The poller needs an MQTT broker.** With none, the core retries in the background and
  `/healthz` still answers `ok`, and `Published discovery` is logged either way. Only the
  `MQTT connected` line below proves the broker.

There are no test credentials. `renault-api` has no sandbox and authenticates only against
production Gigya/Kamereon, so blackhole the three real hosts and the boot test never reaches
Renault. The verification signal is identical; only the poll error text changes. The S3 host is
easy to miss: `get_api_keys()` hits it before login even starts.

```sh
docker buildx build --platform linux/amd64 -t r5-local renault_5

docker network create r5v
printf 'listener 1883 0.0.0.0\nallow_anonymous true\n' > /tmp/mosq.conf
docker run -d --name r5-mqtt --network r5v \
  -v /tmp/mosq.conf:/mosquitto/config/mosquitto.conf eclipse-mosquitto:2

docker run -d --name r5-verify --network r5v -p 8099:8099 \
  --add-host accounts.eu1.gigya.com:127.0.0.1 \
  --add-host api-wired-prod-1-euw1.wrd-aws.com:127.0.0.1 \
  --add-host renault-wrd-prod-1-euw1-myrapp-one.s3-eu-west-1.amazonaws.com:127.0.0.1 \
  -e R5_USERNAME=stub@example.invalid -e R5_PASSWORD=stub-not-a-real-credential \
  -e R5_ACCOUNT_ID=0000000000 -e R5_VIN=VF1STUBVIN0000000 \
  -e R5_LOCALE=en_GB -e R5_POLL_INTERVAL=300 -e R5_BATTERY_CAPACITY_KWH=52 \
  -e R5_STALE_HOURS=6 -e R5_PUBLISH_LOCATION=true -e R5_GPS_PRECISION=4 \
  -e R5_CAR_RENDER= \
  -e R5_LOG_LEVEL=debug -e R5_DEBUG_DUMP=false \
  -e R5_DEPLOY_DASHBOARD=none -e R5_REDEPLOY_DASHBOARD=false \
  -e MQTT_HOST=r5-mqtt -e MQTT_PORT=1883 -e MQTT_USER= -e MQTT_PASS= \
  --entrypoint python3 r5-local -u /app/main.py

sleep 8
curl -s http://127.0.0.1:8099/healthz; echo
docker logs r5-verify 2>&1 | tail -20

docker rm -f r5-verify r5-mqtt; docker network rm r5v
```

Expect `/healthz` to return `ok`, plus `MQTT connected — subscribed to commands, discovery
(re)published` and a `Published discovery: N sensors …` line. One `Poll failed … Cannot connect
to host accounts.eu1.gigya.com` is expected and correct: it is the proof no traffic left the
machine. With the broker stopped before the add-on starts, expect `MQTT broker … unreachable —
retrying` and then `MQTT connected` once it is up.

Exceptions (CI is enough): docs-only, CI-YAML-only, or test-only changes.

## Supervisor pilot (a290's ADR 0002)

`.github/workflows/supervisor.yml` runs `scripts/supervisor-pilot.sh`, a port of the a290
twin's pilot (keep them in lockstep). It installs this add-on under a real stable Supervisor in
the official devcontainer on amd64 and aarch64, with current stable Core and the
`homeassistant:` minimum from `config.yaml`. It checks versions, provenance, `/healthz`, the
HEALTHCHECK, retained MQTT discovery and availability, refused Renault egress, that the standard
dashboard the pilot's options deploy (`deploy_dashboard: standard`, set by the pilot because r5's
default is `none`, at `dashboard_url_path`) exists in Core with views, read back through Core's
Lovelace WebSocket API from inside the add-on container (the Supervisor's Core proxy admits only
an add-on token) with no `Dashboard auto-deploy skipped` in the log, and that AppArmor enforces
`local_renault_5`. All four legs are **required checks** (a290's ADR 0002 row 5): the trigger is
unconditional and a `changes` job decides relevance the way `ui-tests.yaml` does, so a PR that
touches neither the pilot nor the add-on's runtime files still reports four green checks in
seconds, with every step skipped, instead of leaving a required check pending. The traps it
solves are described in a290's CONTRIBUTING.md and ADR 0002. Local run: `PILOT_APPARMOR_CHECK=skip
scripts/supervisor-pilot.sh all` (`CORE_VERSION=minimum` for the floor;
`PILOT_BREAK=start|apparmor|dashboard` breaks it on purpose, and `PILOT_IMAGE=<ref>` installs an
already-published image instead of building the checkout).

## Release / versioning

**Releases are batched and cut on request** ([ADR 0001](docs/adr/0001-release-on-request.md),
mirroring a290's ADR 0006). A user-facing change adds its entry under `## Unreleased` in
**`renault_5/CHANGELOG.md`** and **leaves `renault_5/config.yaml` `version` alone**: the
Supervisor keys updates on that version and `release.yaml` publishes an image for any PR that
moves it, so moving it is releasing. The docs-sync check fails a PR that moves the version
without being exactly a release, and no label waives that. `main.py` reads its version from
`R5_VERSION`, which the Dockerfile sets from `BUILD_VERSION`, so there is no version literal in
code to keep in sync.

Only when the maintainer asks for a release: `just release <version>` (read the entries it
prints, then choose the number: a new feature is a minor, otherwise a patch), commit the two
files it changes as their own PR titled `chore(release): <version>`, and let the existing gates
run. That PR publishes the image, then installs the **published** per-arch image under a real
stable Supervisor on four legs before it may merge, so the artefact users pull has been booted on
both arches at both ends of the Core range. Merging tags `v<version>`. Never put a version bump in a
feature PR "to get it out". r5 is released separately and never ahead of a290.

**What reaches users on merge, whatever the version.** Batching holds back the container image
only. The Supervisor reads the add-on's *files* straight from `main`, so a merge that changes
`config.yaml` (options, schema, `image`, `arch`, `slug`), `build.yaml`, `apparmor.txt`, `DOCS.md`
or `CHANGELOG.md` is visible to users immediately, while they still run the previous image. Such
a change must work with the currently released image. Adding an option the old image ignores is
fine. Removing or renaming one, changing `image`/`arch`/`slug`, or adding an `apparmor.txt` rule
the old image does not need, is not, and belongs in the release PR's company, never in an
ordinary merge. The docs-sync check refuses any `config.*` or `build.*` file other than
`renault_5/config.yaml`, because the Supervisor treats every one it finds as an add-on. Feature
branches are **squash-merged** to `main` and deleted once merged.

## Gotchas

- **MQTT entity ids.** HA ignores the discovery `object_id`; the real `entity_id` is
  `slug(device name + " " + friendly name)`. Derive ids (for dashboards and tests) from the
  *names*, not from `object_id`.
- **Secrets never get logged.** The credentials (My Renault username/password, VIN, account_id,
  GPS) are sensitive. `debug_dump: true` logs decoded API responses but masks them first (the
  core's `renault_mqtt.debug` key masking plus `renault_mqtt.config.redact`, also applied to every
  log line and traceback). Never add a logging path that bypasses it, and never lift the
  `renault_api` logger clamp in `main.py` (`setup_logging`), which holds those loggers at INFO
  or above whatever `log_level` says. At DEBUG, renault-api (checked at 0.5.13; its Kamereon
  logging module is unchanged in 0.6.1) logs every Kamereon request URL and body, every full
  response body (unrounded GPS, the person/account record) and, for a locale it doesn't bundle,
  the fetched API-key config. The root log filter masks only the configured VIN, account id and
  credentials in those lines; GPS and the response bodies pass through. The Gigya JWT is sent as
  a header and is not logged. The clamp keeps those records from ever being created; `debug_dump` is the redacted
  route.
- **Dashboards live under `renault_5/dashboards/`**, bundled into the image (`COPY
  dashboards/*.txt` in the Dockerfile) and read locally by `deploy.py` (no runtime
  raw.githubusercontent.com fetch), as in the A290 add-on. Images are served via the jsDelivr
  CDN (`renault_5/dashboards/Images/...` at the version tag). They keep Topolino65's design, but
  the entity ids are this add-on's own (`sensor.r5_*`): Topolino65's dashboards use
  `_api`-suffixed template sensors and `script.` controls, so they do not bind to this add-on
  (checked 2026-09-26). Typography is intentionally uniform across tabs (no per-screen font/size
  changes); overflow is handled by `white-space:normal` clean-word-break wrapping, not by
  shrinking text. The `reference/` upstream is sourced for assets only and is never committed.
