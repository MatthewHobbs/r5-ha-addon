# Changelog

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
- Includes a **Start-Charging** button, charge-session tracking (last-charge stats,
  charge-type classification), plug stuck-detection, and health sensors (auth-failure,
  data-stale). State persists to `/data/state.json`.
- **Cabin Temperature** (`internalTemperature`) is published — the R5 populates it
  (unlike the A290), though often only shortly after HVAC activity.
