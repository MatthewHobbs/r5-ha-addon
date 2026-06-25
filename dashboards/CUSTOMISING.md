# Customising your R5 (trim & colour)

The dashboards show a render of a Renault 5. By default it's the **Pop Yellow Techno**.
This guide shows how to swap it for the render that matches **your** car's trim and colour
(unlike the Alpine A290, the R5 ships in several trims/colours). All renders are included
in [`Images/Models/`](Images/Models).

## Available renders

Files are named **`<colour>-<trim>.png`** inside `Images/Models/<Trim>/`:

| Trim | Colours (file name) |
| --- | --- |
| **Evolution** | `artic-white-evolution`, `black-evolution`, `pop-green-evolution`, `pop-yellow-evolution` |
| **Iconic** | `artic-white-iconic`, `black-iconic`, `black-iconic-titanium`, `midnight-blue-iconic`, `pop-yellow-iconic` |
| **Roland Garros** | `artic-white-roland-garros`, `black-roland-garros`, `matte-grey-roland-garros`, `midnight-blue-roland-garros` |
| **Techno** | `artic-white-techno` (+ `-black-roof`), `black-techno` (+ `black-techno-red-trim`), `midnight-blue-techno`, `pop-green-techno` (+ `-black-roof`), `pop-yellow-techno` (+ `-black-roof`) |

The dashboards reference the car render as **`r5_background.png`** (the main image) and
**`r5_side.png`** (a smaller side tile on the Bubble dashboard).

## Option A — you copied the images into `/config/www/backgrounds/` (manual install)

1. Pick your render, e.g. `Models/Iconic/midnight-blue-iconic.png`.
2. Using the **File Editor** add-on or **Samba**, copy it into `/config/www/backgrounds/`
   **twice**, renamed to `r5_background.png` and `r5_side.png`.
3. Hard-refresh the dashboard (Ctrl/Cmd-Shift-R). Done — no YAML editing needed.

## Option B — the add-on auto-deployed the dashboard (`deploy_dashboard`)

The render is served from this repo's CDN (Pop Yellow Techno by default), so swap it by
editing the deployed dashboard:

1. Open the dashboard → **⋮ → Edit Dashboard → ⋮ → Raw configuration editor**.
2. Find each **`/local/backgrounds/r5_background.png`** (and `r5_side.png`) and replace it
   with your render's CDN URL:
   ```
   https://cdn.jsdelivr.net/gh/MatthewHobbs/r5-ha-addon@main/dashboards/Images/Models/<Trim>/<colour>-<trim>.png
   ```
   e.g. `…/Models/Roland%20Garros/midnight-blue-roland-garros.png`
   (URL-encode the space in **Roland Garros** as `%20`).
3. **Save.** (Re-running `deploy_dashboard` won't overwrite your edits — it's create-once.)

## Optional — a colour-matched map marker

The dashboards use Home Assistant's built-in `map` card (a plain pin). If you'd like the
car-coloured marker the original project used, add this to `configuration.yaml` and
restart, picking the marker that matches your colour:

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
