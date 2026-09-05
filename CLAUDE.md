# CLAUDE.md

Home Assistant **add-on** for the **Renault 5 E-Tech**. It polls the Renault/Kamereon API
(`renault-api`) on an asyncio loop and publishes `sensor.r5_*` / `binary_sensor.*` /
`button.*` / `number.*` entities over **MQTT auto-discovery** — no shell scripts, no `venv`,
no `secrets.yaml`. Credentials are entered on the add-on's Configuration page. It continues
[`Topolino65/renault-5-dashboard-view`](https://github.com/Topolino65/renault-5-dashboard-view)
(full credit for the original dashboards, assets and design), replacing that project's
fragile `venv` + `renault-api` CLI + shell-script data layer.

**Tier 0.** Global rules (dual review, container-verify, trunk/merge policy, Conventional
Commits, HA cadence) live in `~/.claude/CLAUDE.md`; this file is R5-specifics only.

A sibling repo, **`MatthewHobbs/a290-ha-addon`**, is the Alpine A290 add-on this is **ported
from** (the R5 E-Tech and A290 share the CMF-BEV / KCM platform). **Keep the two in
lockstep** — most feature/fix work here should be mirrored there (adjusting for per-model
API differences), and vice-versa.

## Layout

```
renault_5/                   the add-on (this is what HA installs)
  app/
    main.py                  asyncio poller, MQTT discovery, controls, charge-limit numbers,
                             debug_dump, health endpoint (/healthz). State (plug
                             stuck-detection, charge-session tracking, health) persists to
                             /data/state.json.
    catalog.py               entity tables — SENSORS / BINARY_SENSORS / ICONS / NUMBERS /
                             ACTION_BUTTONS / OPTIONAL_ENDPOINTS / RETIRED_SENSORS /
                             DEFAULT_DISABLED_SENSORS / SOC_ENDPOINT (extracted from main.py
                             to match the A290 add-on's structure — lockstep).
    deploy.py                optional dashboard auto-deploy via the HA core API
    requirements.txt         pinned deps (see "Dependencies")
  tests/                     pytest — conftest.py, test_main.py, test_runtime.py
  config.yaml                add-on manifest: version, options + schema
  Dockerfile                 pinned base (FROM ghcr.io/home-assistant/base:<tag>), HEALTHCHECK, root user
  run.sh                     bashio entrypoint (reads /data/options.json)
  DOCS.md / CHANGELOG.md     the add-on's HA docs page + changelog
  dashboards/                front-end.txt + front-end-bubble.txt + Images/ — bundled into the
                             image (COPYed in the Dockerfile); deploy.py reads the *.txt
                             locally, images served from the jsDelivr CDN (like the A290)
ui-tests/                    containerized HA + Playwright responsive/overflow gate
docs/                        dashboards-on-mobile.md + screenshots (user docs)
reference/                   local-only Topolino65 upstream — sourced for assets, gitignored,
                             NEVER committed
ruff.toml / repository.yaml / README.md / LICENSE
```

## Dependencies

`renault_5/app/requirements.txt` — all pinned, keep them pinned:
`renault-api==0.5.12`, `paho-mqtt==2.1.0`, `PyYAML==6.0.3`.

**Do not bump `renault-api` casually.** Per-model endpoint support is hard-coded in the
library at `renault_api/kamereon/models.py` → `_VEHICLE_ENDPOINTS` (R5 is model `R5E1VE`,
A290 is `A5E1AE`). That map — not the readthedocs pages — is the authoritative source for
what each car exposes. The R5 supports **all six native controls** — charge-start (KCM
instant-charge), flash lights, sound horn, HVAC start/stop, and refresh location —
**unlike the A290, which forbids charge-start**; charge-mode and tyre-pressure are
**forbidden** on the R5 (`R5E1VE`) and are not published.
The add-on probes `supports_endpoint()` at startup and only publishes what's available.

**Platform caveats (R5 E-Tech / CMF-BEV, KCM):** `batteryCapacity` is always 0 (the add-on
uses the configured capacity); `chargingStatus` is a float `ChargeState` (decoded via the
library enum, not a plain 0/1/-1); `chargingInstantaneousPower` units are unreliable;
`batteryTemperature` is sometimes absent.

## Local checks — run the FULL suite before pushing

CI (`.github/workflows/ci.yaml`) has four jobs: **lint, test, security, build**. Run all of
them locally before pushing — not just ruff + pytest. macOS vs Linux behaviour differs
(the UI gate has caught Linux-only font truncations a local macOS run missed), so a green
local partial run is not a green CI.

```sh
# lint
ruff check renault_5/app
yamllint -c .yamllint renault_5 repository.yaml
hadolint -c .hadolint.yaml renault_5/Dockerfile
shellcheck renault_5/run.sh

# test (coverage gate is 90%)
python3 -m pytest renault_5/tests -q --cov=renault_5/app --cov-report=term-missing --cov-fail-under=90

# security
bandit -r renault_5/app -ll
pip-audit -r renault_5/app/requirements.txt
trivy fs --scanners vuln,misconfig,secret --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed .
```

The `ui-tests/` gate (its own `ui-tests.yaml` workflow, path-filtered to dashboards +
ui-tests) is run with `ui-tests/run.sh` — it boots a throwaway HA container, seeds entities,
and uses Playwright across ~10 phone viewports to fail on any text truncation or
`hui-error-card`. Run it whenever you touch `renault_5/dashboards/` or `ui-tests/`.

The harness **pins its render inputs** in `ui-tests/run.sh` so the gate is deterministic — HA
image `2026.7.1`, mushroom `v5.1.1`, button-card `v7.0.1`, card-mod `v4.2.1`. A floating
`:stable`/`@latest`/`@master` previously drifted under the gate and made it flake (marginal
sub-pixel truncations on one high-DPR device that vanished on the next run). Bump all four
**together and deliberately** when tracking upstream; the gate confirms the new combo renders
clean. Keep them identical to the a290 twin's `ui-tests/run.sh` (lockstep).

Ruff config (`ruff.toml`): line-length 120, target py314, `select = E,F,W,B,I`,
`ignore = E501,B008`.

## Before recommending a merge: build and boot the container locally

Per the global container rule — this add-on's image is pulled by tag (`config.yaml` `version`),
so build and boot it locally and observe the changed behaviour before merge on any runtime PR.

Two things make a naive run fail, both worth knowing before you spend time debugging them:

- **`bashio::config` reads the Supervisor API, not `/data/options.json`.** Mounting a stub
  options file achieves nothing: `/run.sh` gets empty config and the add-on correctly exits
  with `Missing required setting`. Bypass `run.sh` with `--entrypoint python3` and pass the
  `R5_*` env vars directly.
- **The poller needs an MQTT broker** or it dies on `ConnectionRefusedError` before it ever
  serves `/healthz`.

There are no test credentials to use. `renault-api` has no sandbox and authenticates only
against production Gigya/Kamereon, so blackhole the three real hosts and the boot test never
reaches Renault. The verification signal is identical; only the poll error text changes.
The S3 host is easy to miss — `get_api_keys()` hits it before login even starts.

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
machine.

Exceptions (CI is enough): docs-only, CI-YAML-only, or test-only changes.

## Release / versioning

Any user-facing change bumps **`renault_5/config.yaml` `version`** AND the **`VERSION`
constant in `renault_5/app/main.py`** (keep them in sync) and adds a
**`renault_5/CHANGELOG.md`** entry (Supervisor keys the update on the version). When
mirroring to `a290-ha-addon`, bump **`alpine_a290/config.yaml`** there. Feature branches are
**squash-merged** to `main` and deleted once merged.

## Gotchas

- **MQTT entity ids.** HA ignores the discovery `object_id`; the real `entity_id` is
  `slug(device name + " " + friendly name)`. Derive ids (e.g. for dashboards/tests) from
  the *names*, not from `object_id`.
- **Secrets never get logged.** The credentials (My Renault username/password, VIN,
  account_id, GPS) are sensitive. `debug_dump: true` logs decoded API responses but routes
  everything through `_debug_redact` first; never add a logging path that bypasses it, and
  never use `log_level: debug` for diagnosis (the library prints access tokens at that
  level — `debug_dump` exists precisely to avoid that).
- **Dashboards live under `renault_5/dashboards/`** — bundled into the image (`COPY
  dashboards/*.txt` in the Dockerfile) and read locally by `deploy.py` (no runtime
  raw.githubusercontent.com fetch), aligned with the A290 add-on. Images are still served via
  the jsDelivr CDN (`renault_5/dashboards/Images/...` at the version tag). They keep
  Topolino65's naming (modernised, locale-aware). Typography is intentionally uniform across
  tabs (no per-screen font/size changes); overflow is handled by `white-space:normal`
  clean-word-break wrapping, not by shrinking text. The `reference/` upstream is sourced for
  assets only and is never committed.
