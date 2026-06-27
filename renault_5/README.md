# Renault 5

A maintained Home Assistant add-on for the **Renault 5 E-Tech**, continuing
[Topolino65/renault-5-dashboard-view](https://github.com/Topolino65/renault-5-dashboard-view)
(full credit for the original dashboards, assets and design). It replaces that project's
`venv` + `renault-api` CLI + shell-script layer with a proper add-on that polls the
Renault/Kamereon API and publishes `sensor.r5_*` entities over **MQTT auto-discovery** — no
scripts, no `secrets.yaml`. Credentials are entered once on the **Configuration** tab.

- **Native controls** — charge, lights, horn, HVAC, refresh location — plus **writable
  charge-limit sliders**. You do **not** need Home Assistant's `renault` integration.
- **Ready-made dashboards** bundled in: set `deploy_dashboard` and the add-on installs a
  **standard** or **Bubble Card** dashboard for you (both phone-verified in CI).
- **Pre-built, Cosign-signed image** pulled by the Supervisor — no slow on-device build.

> **Untested on a real Renault 5** — back-ported from the
> [Alpine A290 add-on](https://github.com/MatthewHobbs/a290-ha-addon) (the R5 E-Tech and A290
> share the CMF-BEV / KCM platform), which runs on the maintainer's car. Please
> **[open an issue](https://github.com/MatthewHobbs/r5-ha-addon/issues)** with how it goes.

See **[DOCS.md](DOCS.md)** for the full option/entity reference and setup, and the
[repository README](https://github.com/MatthewHobbs/r5-ha-addon) for the **HACS frontend
cards you must install first** (card-mod, Mushroom, Button Card, Browser Mod, Bubble Card).
