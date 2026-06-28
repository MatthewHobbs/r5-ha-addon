# Customising your R5 (trim & colour)

The dashboards show a render of a Renault 5. By default it's the **Pop Yellow Techno**.
Here's how to match **your** car's trim and colour (unlike the Alpine A290, the R5 ships
in several trims/colours). All renders are included in [`Images/Models/`](Images/Models).

## Easiest — the `car_render` dropdown (auto-deploy)

If the app installs the dashboard for you (`deploy_dashboard: standard|bubble`), you
don't need to touch any files:

1. **Settings → Apps → Renault 5 → Configuration → `car_render`** — pick your trim/colour
   (e.g. `midnight-blue-iconic`).
2. Set **`redeploy_dashboard: true`** and **Restart** the app so the dashboard
   re-deploys with your render. (Set it back to `false` afterwards if you want your manual
   dashboard edits protected.)

That's it — the matching render is served straight from the CDN, nothing to copy or edit.

## Available renders

Files are named **`<colour>-<trim>.webp`** inside `Images/Models/<Trim>/` — and those
file-name stems are exactly the `car_render` dropdown values:

| Trim | Colours (`car_render` value) |
| --- | --- |
| **Evolution** | `artic-white-evolution`, `black-evolution`, `pop-green-evolution`, `pop-yellow-evolution` |
| **Iconic** | `artic-white-iconic`, `black-iconic`, `black-iconic-titanium`, `midnight-blue-iconic`, `pop-yellow-iconic` |
| **Roland Garros** | `artic-white-roland-garros`, `black-roland-garros`, `matte-grey-roland-garros`, `midnight-blue-roland-garros` |
| **Techno** | `artic-white-techno`, `artic-white-techno-black-roof`, `black-techno`, `black-techno-red-trim`, `midnight-blue-techno`, `pop-green-techno`, `pop-green-techno-black-roof`, `pop-yellow-techno`, `pop-yellow-techno-black-roof` |

## Manual install — swap the image files

If you copied the images into `/config/www/backgrounds/` yourself (rather than using
auto-deploy), the dashboards reference the render as **`r5_background.webp`** (main image)
and **`r5_side.webp`** (the Bubble side tile):

1. Pick your render, e.g. `Models/Iconic/midnight-blue-iconic.webp`.
2. With the **File Editor** app or **Samba**, copy it into `/config/www/backgrounds/`
   **twice**, renamed `r5_background.webp` and `r5_side.webp`.
3. Hard-refresh the dashboard (Ctrl/Cmd-Shift-R). No YAML editing needed.

## Optional — a colour-matched map marker

The dashboards use Home Assistant's built-in `map` card (a plain pin). For the car-coloured
marker the original project used, add this to `configuration.yaml` and restart, picking the
marker that matches your colour:

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
