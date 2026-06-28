# CLAUDE.md

Home Assistant **add-on** for the **Renault 5 E-Tech**. It polls the Renault/Kamereon API
(`renault-api`) on an asyncio loop and publishes `sensor.r5_*` / `binary_sensor.*` /
`button.*` / `number.*` entities over **MQTT auto-discovery** — no shell scripts, no `venv`,
no `secrets.yaml`. Credentials are entered on the add-on's Configuration page. It continues
[`Topolino65/renault-5-dashboard-view`](https://github.com/Topolino65/renault-5-dashboard-view)
(full credit for the original dashboards, assets and design), replacing that project's
fragile `venv` + `renault-api` CLI + shell-script data layer.

A sibling repo, **`MatthewHobbs/a290-ha-addon`**, is the Alpine A290 add-on this is **ported
from** (the R5 E-Tech and A290 share the CMF-BEV / KCM platform). **Keep the two in
lockstep** — most feature/fix work here should be mirrored there (adjusting for per-model
API differences), and vice-versa.

## Layout

```
renault_5/                   the add-on (this is what HA installs)
  app/
    main.py                  asyncio poller, MQTT discovery, controls, charge-limit numbers,
                             debug_dump, health endpoint (/healthz). The entity tables
                             (SENSORS / BINARY_SENSORS / ACTION_BUTTONS / NUMBERS, endpoint
                             constants, RETIRED_* cleanup lists) live here — there is NO
                             separate catalog.py (unlike the A290 add-on). State (plug
                             stuck-detection, charge-session tracking, health) persists to
                             /data/state.json.
    deploy.py                optional dashboard auto-deploy via the HA core API
    requirements.txt         pinned deps (see "Dependencies")
  tests/                     pytest — conftest.py, test_main.py, test_runtime.py
  config.yaml                add-on manifest: version, options + schema
  build.yaml                 base-image map for the multi-arch build
  Dockerfile                 alpine base, HEALTHCHECK, root user
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

Ruff config (`ruff.toml`): line-length 120, target py311, `select = E,F,W,B,I`,
`ignore = E501,B008`.

## Reviews — Claude + codex, compared

Every review of a change here — PR review, diff review, pre-merge review, security review —
is run **twice: once by Claude and once independently by `codex`** — and the two results
reconciled. A single reviewer misses things and can be confidently wrong; two independent
passes surface contradictions and errors that one alone won't, and the comparison is the
point (not just running both).

- `codex exec review --base <branch>` (PR vs base), `codex exec review --uncommitted`
  (working tree), or `codex exec review --commit <sha>` — run from the repo root.
- Compare the two explicitly: where they agree, where they contradict, and anything one
  caught that the other missed. Reconcile — don't just stack both.
- Surface contradictions and errors rather than silently picking one.

## Before recommending a merge: build the container locally

This add-on ships as a container image that HA Supervisor pulls **by tag** (`config.yaml`
`version`), so the production platform can't be live-verified until *after* a release is
tagged and published. A version-bump / runtime PR is therefore **not** considered verified
by CI alone — build and boot the image locally and observe the changed behaviour first:

```sh
docker buildx build --platform linux/amd64 \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.19 \
  -t r5-local renault_5
# then run with a stub /data/options.json and curl http://localhost:<port>/healthz, check logs, etc.
```

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
