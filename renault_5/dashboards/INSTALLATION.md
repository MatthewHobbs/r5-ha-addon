# Renault 5 Dashboard — Installation

> **Back up Home Assistant before you start** (Settings → System → Backups). These
> steps add a dashboard (and, for the manual route, packages and helpers) to your config.
>
> **Charging control:** the app publishes a **Start Charging** button that clears the car's
> **built-in** charge programs. Under external scheduling (e.g. **Octopus Intelligent**), that
> was a no-op on an A290; the same outcome is inferred for R5 from the shared API path, not
> observed on an R5. Use your charger's boost/bump control instead. Remote charge-*stop* stays
> unavailable. The standard dashboard retains a greyed-out Start Charging tile; Bubble has no
> active charge-start control.

This dashboard is the **frontend only**. All car data is provided by the
**[Renault 5 app](https://github.com/MatthewHobbs/r5-ha-addon)** — a proper
Home Assistant app that polls the Renault/Kamereon API and publishes
`sensor.r5_*` entities over MQTT auto-discovery. Credentials are entered once on the Configuration page, nothing to edit by hand.

**The app can also install this dashboard for you.** That's the recommended route
below — it copies nothing into `/config/www`, edits no `configuration.yaml`, and needs no
raw-editor paste. The [manual install](#manual-install-advanced) is kept as a fallback.

---

## Recommended install (app auto-deploy)

### 1. Install the dependencies first

Install these **before** the app, so a deployed dashboard renders straight away (a
dashboard deployed before its cards exist shows a wall of "custom element doesn't exist"):

- **Mosquitto broker** — Settings → Apps → App Store. The app auto-discovers it.
- **[HACS](https://hacs.xyz)** → Frontend, then these cards:
  - **card-mod**, **Mushroom**, **Button Card**, **Browser Mod**
  - **Bubble Card** — only if you want the Bubble dashboard

The location map uses Home Assistant's built-in `map` card, so there's no map plugin or
API key to install.

### 2. Install + configure the app (the data layer)

1. **Settings → Apps → App Store → ⋮ (top-right) → Repositories**, and add:
   ```
   https://github.com/MatthewHobbs/r5-ha-addon
   ```
2. Install the **Renault 5** app. Open its **Configuration** tab and set:
   | Option | Value |
   | --- | --- |
   | `username` / `password` | Your **My Renault** app login. |
   | `vin` | Your vehicle VIN (uppercase). |
   | `account_id` | Leave **blank** to auto-discover (set only if you have several accounts). |
   | `locale` | e.g. `en_GB`. Sets the API region, drive side (RHD for `en_GB`/`en_IE`) and units (**miles for `en_GB`**, km otherwise). |
   | `battery_capacity_kwh` | `52` or `40`. |
   | `poll_interval` | Seconds between polls (default 300). |
3. **Start** the app. Within a minute you should see `sensor.r5_battery_level`,
   `…_battery_autonomy`, `…_charger_plug_status`, `device_tracker.r5_location`, etc.
   (Settings → Devices & Services → Entities, filter "r5".)

See the app's [DOCS](https://github.com/MatthewHobbs/r5-ha-addon/blob/main/renault_5/DOCS.md)
for the full entity list.

### 3. Dashboard auto-deploy

Auto-deploy is **on by default**: `deploy_dashboard` is `standard`. On the app's
**Configuration** tab you can change these options:

| Option | Value |
| --- | --- |
| `deploy_dashboard` | `standard` (default), `bubble`, `both`, or `none` to manage dashboards yourself. |
| `dashboard_url_path` | URL slug for the dashboard (default `renault-5`; with `both`, the bubble one is suffixed `-bubble`). |
| `car_render` | Default `pop-yellow-techno`; see [Customising your R5](#customising-your-r5-trim--colour). |

Existing users who selected `none` keep `none`; change it explicitly to opt in.

If you change them, **restart** the app. On start it reads the dashboard YAML **bundled in the app**,
rewrites its images to the **jsDelivr CDN** (served from this repo), registers the **Zen
Dots** Google font as a Lovelace resource, and creates the dashboard via the HA API — then
it appears in your sidebar.

It is **create-once**: if the dashboard already exists it's left untouched (your edits are
safe). To pull in a later layout update, or to switch an already-deployed `standard` to
`bubble` at the same `dashboard_url_path`, set `redeploy_dashboard: true`, restart, then set
it back to `false`: while it is on, every start overwrites the dashboard and your edits.

### 4. Control buttons + optional extras

- **Control buttons:** the app publishes these **natively over MQTT** — no Home Assistant `renault` integration needed: `button.r5_sound_horn`, `…_flash_lights`,
  `…_start_air_conditioner`, `…_stop_air_conditioner`, `…_start_charging`. Each is gated
  on what the platform supports. `…_refresh_location` is **not published by default** — it
  needs `enable_refresh_location: true` (see DOCS.md). On the shared platform it can replace a
  parked car's saved position with "no fix" until the next journey (observed on an A290, not an R5).
  Charge-start and the retained greyed-out tile are described above; charge-*stop* stays unavailable.
- **Pretty location** and **test mode** are not auto-deployed — they're a small package
  you merge manually. See [Optional extras](#optional-extras).

---

## Manual install (advanced)

Use this if you'd rather host the assets yourself (no CDN dependency) or want full local
control. Do steps 1–2 of the recommended route first (app + HACS cards), and set
`deploy_dashboard: none` on the app's Configuration tab **before you first start it** (the default
deploys the standard dashboard on start), then:

### 3. Copy the assets to `/config/www/`

Using the **File Editor** app or **Samba**:

1. Create `/config/www/backgrounds/` and copy everything from this repo's `Images/`
   subfolders into it (the dashboards reference them as `/local/backgrounds/…`).
2. Copy `CSS/zen-dots.css` to `/config/www/zen-dots.css`.
3. Copy `Fonts/ZenDots-Regular.ttf` to `/config/www/fonts/` (the CSS loads it from
   `/local/fonts/`).

### 4. Wire up resources (and any extras)

The block below shows what to merge into your
`configuration.yaml` (adjust paths to where you place the files):

```yaml
lovelace:
  resources:
    - url: /local/zen-dots.css
      type: css

homeassistant:
  packages: !include_dir_named packages

template: !include_dir_merge_list templates   # pretty-location sensor
```

Copy the repo folders accordingly:

- `Packages/` → `/config/packages/` — includes the self-contained **`r5_test.yaml`** test-mode
  package (its own helpers, display sensors, timers and driver — nothing else to wire up).
- `Templates/` → `/config/templates/` — the pretty-location sensor.

Check **Developer Tools → YAML → Check Configuration**, then **Restart**.

### 5. Import the dashboard

1. **Settings → Dashboards → + Add Dashboard → New dashboard from scratch.**
2. Open it, then **⋮ → Edit Dashboard → ⋮ → Raw configuration editor**.
3. Replace the editor contents with `views:` on the first line, then paste the entire contents
   of [`front-end.txt`](front-end.txt) (standard) or [`front-end-bubble.txt`](front-end-bubble.txt)
   below it, keeping the file's indentation. Both files contain a list of views; the editor needs
   a dashboard object containing that list. **Save**.

---

## Optional extras

Whichever route you used, these are a small manual package (the auto-deploy doesn't
install them):

- **Pretty location:** `sensor.r5_pretty_location` shows "Driveway" / "Home" /
  town. Merge `Templates/r5_pretty_location.yaml` as in
  [manual step 4](#4-wire-up-resources-and-any-extras), create a `zone.driveway` for the
  driveway state, and optionally the [`places`](https://github.com/custom-components/places)
  integration for town names.
- **Test mode:** merge the single self-contained **`Packages/r5_test.yaml`** (everything is
  prefixed `r5_test_*`, so it never clashes with the A290 add-on's test mode). Then either toggle
  `input_boolean.r5_test_mode` to preview the charge panels, or press the **Run Test Charge**
  button for a scripted ~5-minute demo that fills the Last-Charge tiles and auto-resets.

### Migrating an existing R5 test package

After editing an already loaded package, reload the affected templates and automations/helpers
where supported, or restart Home Assistant to reload them all. If migrating an older upstream
package, replace its R5 test definitions first and update any `sensor.test_*` references to
`sensor.r5_test_*`; **Energy Stored** is `sensor.r5_test_last_charge_energy_stored`. Remove stale
bare registry entries only after replacing the YAML, and keep anything another dashboard uses.
Re-copy or redeploy an existing dashboard to pick up the new references.

---

## Troubleshooting

- **No `r5` entities?** Check the app log (Settings → Apps → Renault 5
  → Log). A `403` usually means wrong credentials/locale. Confirm Mosquitto is running.
- **Entities show but the dashboard is blank/red:** a HACS card is missing — recheck
  the prerequisites and hard-refresh the browser. If you auto-deployed before installing
  the cards, install them, set `redeploy_dashboard: true`, restart, then set it back to `false`.
- **Map empty:** confirm `device_tracker.r5_location` has a location (the
  app must have polled at least once with GPS data).

## Customising your R5 (trim & colour)

The dashboards show a render of a Renault 5. By default it's the **Pop Yellow Techno**.
Here's how to match **your** car's trim and colour (unlike the Alpine A290, the R5 ships
in several trims/colours). All renders are included in [`Images/Models/`](Images/Models).

### Easiest — the `car_render` dropdown (auto-deploy)

If the app installs the dashboard for you (`deploy_dashboard: standard|bubble|both`), you
don't need to touch any files:

1. **Settings → Apps → Renault 5 → Configuration → `car_render`** — pick your trim/colour
   (e.g. `midnight-blue-iconic`).
2. Set **`redeploy_dashboard: true`** and **Restart** the app so the dashboard
   re-deploys with your render. Then set it back to `false`: while it is on, every start
   overwrites the dashboard and your edits.

That's it — the matching render is served straight from the CDN, nothing to copy or edit.

### Available renders

Files are named **`<colour>-<trim>.webp`** inside `Images/Models/<Trim>/` — and those
file-name stems are exactly the `car_render` dropdown values:

| Trim | Colours (`car_render` value) |
| --- | --- |
| **Evolution** | `artic-white-evolution`, `black-evolution`, `pop-green-evolution`, `pop-yellow-evolution` |
| **Iconic** | `artic-white-iconic`, `black-iconic`, `black-iconic-titanium`, `midnight-blue-iconic`, `pop-yellow-iconic` |
| **Roland Garros** | `artic-white-roland-garros`, `black-roland-garros`, `matte-grey-roland-garros`, `midnight-blue-roland-garros` |
| **Techno** | `artic-white-techno`, `artic-white-techno-black-roof`, `black-techno`, `black-techno-red-trim`, `midnight-blue-techno`, `pop-green-techno`, `pop-green-techno-black-roof`, `pop-yellow-techno`, `pop-yellow-techno-black-roof` |

### Manual install — swap the image files

If you copied the images into `/config/www/backgrounds/` yourself (rather than using
auto-deploy), the dashboards reference the render as **`r5_background.webp`** (main image)
and **`r5_side.webp`** (the Bubble side tile):

1. Pick your render, e.g. `Models/Iconic/midnight-blue-iconic.webp`.
2. With the **File Editor** app or **Samba**, copy it into `/config/www/backgrounds/`
   **twice**, renamed `r5_background.webp` and `r5_side.webp`.
3. Hard-refresh the dashboard (Ctrl/Cmd-Shift-R). No YAML editing needed.

### Optional — a colour-matched map marker

The dashboards use Home Assistant's built-in `map` card (a plain pin). For the car-coloured
marker the original project used, add this to `configuration.yaml` and restart, picking the
marker that matches your colour (merge this with any existing `homeassistant` section):

```yaml
homeassistant:
  customize:
    device_tracker.r5_location:
      entity_picture: /local/backgrounds/map-marker-pop-yellow.png
```

Available markers (in [`Images/Map Markers/`](Images/Map%20Markers)):
`map-marker-black`, `map-marker-grey`, `map-marker-pop-green`, `map-marker-pop-yellow`,
`map-marker-white`, `rmap-marker-midnight-blue`. Copy your chosen one into
`/config/www/backgrounds/` first (manual install) or reference its CDN URL.
