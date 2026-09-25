<!-- renault-api-watch:__LATEST__ -->

**Pinned:** `__PINNED__` — **latest upstream:** `__LATEST__`
https://github.com/hacf-fr/renault-api/releases/tag/v__LATEST__

This is a notification, not a request to bump. `renault-api` is excluded from both dependency
bots on purpose: per-model endpoint support is hard-coded in
`renault_api/kamereon/models.py` → `_VEHICLE_ENDPOINTS`, so a bump changes runtime behaviour and
needs a container-verified review.

### What to check before bumping

- Diff the `R5E1VE` entry in `_VEHICLE_ENDPOINTS` between `v__PINNED__` and `v__LATEST__`. That
  map, not the readthedocs pages, is the authoritative source for what this car exposes.
  `detect_supported()` in `main.py` probes it at startup, and the result decides what is
  published. At `v0.5.13` the entry allows the six action buttons' endpoints
  (`actions/charge-start` via KCM `ev/settings`, `actions/lights-start`, `actions/horn-start`,
  `actions/hvac-start`, `actions/hvac-stop`, `actions/refresh-location`), `soc-levels` (the
  charge-limit numbers) and `charges` (last-charge reconciliation in the renault-mqtt core), and
  sets `charge-mode` and `pressure` to `None`, so those sensors are never published. A change to
  any of these adds or removes an entity, a button or a behaviour on every R5.
- `hvac-settings` is allowed at `v0.5.13` but is not probed. It sits behind a circuit breaker in
  `main.py` (mirrored from a290 v1.23.1), which reads the endpoint's behaviour, not the table,
  because the A290's server answered `502000` while the library listed the endpoint as supported.
  If a release sets it to `None`, the call raises instead and the breaker trips; a table change
  alone is not a reason to remove the breaker.
- Check the release for changes to the models the poller reads. `tests/test_api_contract.py`
  runs the poller's field reads through the library's real schemas for battery-status, cockpit,
  location, charges and soc-levels, so run the suite against the new pin. It does not cover the
  hvac-status, hvac-settings or `ev/settings` models, and nothing checks that
  `CHARGE_STATUS_LABELS` still maps every `ChargeState` member.
- Bump the pin in **both** `renault_5/app/requirements.in` and the hash-locked
  `renault_5/app/requirements.txt`. CI and the image install from `requirements.txt` only and
  nothing compares the pair, so missing one is silent until the next regeneration reverts it.
- Mirror to `a290-ha-addon` (model `A5E1AE`), which watches the same library; keep the two in
  lockstep.
