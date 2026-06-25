# Renault 5 — Home Assistant add-on + dashboards

A maintained Home Assistant integration for the **Renault 5 E-Tech**, continuing the work
of [**Topolino65/renault-5-dashboard-view**](https://github.com/Topolino65/renault-5-dashboard-view)
— full credit to Topolino65 for the original dashboards, assets and design.

This repo modernises that project by replacing its fragile `venv` + `renault-api` CLI +
shell-script data layer with a proper **Home Assistant add-on** that polls the
Renault/Kamereon API and publishes `sensor.r5_*` entities over **MQTT auto-discovery** —
no scripts, no `secrets.yaml`, credentials entered once on the add-on's config page.

It is ported from the [Alpine A290 add-on](https://github.com/MatthewHobbs/a290-ha-addon)
(the R5 E-Tech and Alpine A290 share the CMF-BEV / KCM platform).

## What's here

- **The add-on:** [`renault_5/`](renault_5/) — the MQTT data layer. See
  [`renault_5/DOCS.md`](renault_5/DOCS.md).
- **The dashboards:** [`dashboards/`](dashboards/) — a **standard** dashboard
  (`front-end.txt`, Topolino's layout repointed to the add-on entities) and a **Bubble
  Card** dashboard (`front-end-bubble.txt`, ported from the A290), both fed by the add-on.
  The add-on can install either for you (`deploy_dashboard: standard|bubble`), or copy them
  in manually. Assets (R5 renders, map markers, Zen Dots font) live under `dashboards/`.

## Install (add-on)

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add:
   ```
   https://github.com/MatthewHobbs/r5-ha-addon
   ```
2. Install **Mosquitto broker** (if you haven't) and the **Renault 5** add-on.
3. On the add-on's **Configuration** tab, set your My Renault `username`/`password`,
   `vin`, `locale`, and `battery_capacity_kwh`, then **Start** it.
4. The `sensor.r5_*` entities appear under a **Renault 5** device within a minute.

## Credits

Original dashboards, assets (car renders, map markers, fonts) and concept by
[**Topolino65**](https://github.com/Topolino65)
([renault-5-dashboard-view](https://github.com/Topolino65/renault-5-dashboard-view)),
MIT-licensed. This project is an independently-maintained continuation; the original
copyright is retained in [LICENSE](LICENSE).
