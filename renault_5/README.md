# Renault 5

A maintained Home Assistant app for the **Renault 5 E-Tech**. Its real purpose is to be
an **updated data layer** for
[Topolino65/renault-5-dashboard-view](https://github.com/Topolino65/renault-5-dashboard-view)
(full credit for the original dashboards, assets and design): it replaces that project's
fragile `venv` + `renault-api` CLI + shell-script layer with a proper app that polls the
[Renault/Kamereon API](https://github.com/hacf-fr/renault-api) and publishes `sensor.r5_*` entities over **MQTT auto-discovery**. The entity ids are the app's own,
so Topolino65's dashboards don't bind to them; use the app's bundled dashboards, a modified
version of Topolino65's UI built for these entities. Credentials
are entered once on the **Configuration** tab.

- **Native controls** — charge, lights, horn, HVAC, and opt-in refresh location — plus **writable
  charge-limit sliders**. You do **not** need Home Assistant's `renault` integration.
- **Location is opt-out** — `device_tracker.r5_location` publishes by default (coarsened per
  `gps_precision`); set `publish_location: false` to fetch none and clear any previously-retained
  GPS from the broker, for a zero location footprint.
- **Bundled dashboards** — a **modified version of Topolino65's UI** built for this app's
  entities, ported from the maintainer's [Alpine A290 app](https://github.com/MatthewHobbs/a290-ha-addon).
  Set `deploy_dashboard` and the app installs a **standard** or **Bubble Card** dashboard for
  you (both phone-verified in CI). It is off by default.
- **Pre-built multi-arch image** pulled by the Supervisor — no slow on-device build (with a
  keyless build-provenance attestation; verify with `gh attestation verify`).

> **Untested on a real Renault 5** — back-ported from the
> [Alpine A290 app](https://github.com/MatthewHobbs/a290-ha-addon) (the R5 E-Tech and A290
> share the CMF-BEV / KCM platform), which runs on the maintainer's car. Please
> **[open an issue](https://github.com/MatthewHobbs/r5-ha-addon/issues)** with how it goes.

See **[DOCS.md](DOCS.md)** for the full option/entity reference and setup, and the
[repository README](https://github.com/MatthewHobbs/r5-ha-addon) for the **HACS frontend
cards you must install first** (card-mod, Mushroom, Button Card, Browser Mod, Bubble Card).
