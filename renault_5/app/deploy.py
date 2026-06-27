"""Optional dashboard auto-deploy.

When `deploy_dashboard` is `standard` or `bubble`, this fetches the chosen
dashboard from this repo (dashboards/ folder), rewrites its `/local/...`
image references to jsDelivr CDN URLs (so nothing has to be copied into
`/config/www`), registers a Zen Dots Google-Font CSS resource, and creates the
dashboard via Home Assistant's WebSocket API and pushes its config.

It is **create-once**: if the dashboard url_path already exists it is left alone
(so user edits are never clobbered) unless `redeploy_dashboard` is true. Every
failure here is non-fatal — the data poller runs regardless.
"""
import asyncio
import logging
import os
import re

import aiohttp
import yaml

LOG = logging.getLogger("renault_5.deploy")

REPO = "MatthewHobbs/r5-ha-addon"
# Pin to the add-on's release tag (set by main.py before run_deploy) for a reproducible
# dashboard; falls back to the default below for an untagged/dev checkout.
REF = os.environ.get("R5_ADDON_REF", "main")
RAW = f"https://raw.githubusercontent.com/{REPO}/{REF}/dashboards"
CDN = f"https://cdn.jsdelivr.net/gh/{REPO}@{REF}/dashboards"
FONT_URL = "https://fonts.googleapis.com/css2?family=Zen+Dots&display=swap"
DASHBOARDS = {"standard": "front-end.txt", "bubble": "front-end-bubble.txt"}

# /local/backgrounds/<file> -> repo path (the dashboards reference images as
# /local/backgrounds/<file>; the repo keeps them in typed subfolders under dashboards/).
IMG_MAP = {
    "r5_background.png": "Images/Background/r5_background.png",
    "r5_side.png": "Images/Background/r5_side.png",
    "charge-indicator.png": "Images/Charging/charge-indicator.png",
}


# Trim folders under Images/Models — R5_CAR_RENDER (e.g. "midnight-blue-iconic") picks one.
_RENDER_TRIMS = (("roland-garros", "Roland%20Garros"), ("evolution", "Evolution"),
                 ("iconic", "Iconic"), ("techno", "Techno"))


def _selected_render():
    """Repo path for the chosen trim/colour render (R5_CAR_RENDER), or None for the default."""
    stem = os.environ.get("R5_CAR_RENDER", "").strip().lower()
    for key, trim in _RENDER_TRIMS:
        if key in stem:
            return f"Images/Models/{trim}/{stem}.png"
    return None


def _cdnify(text):
    """Rewrite /local/backgrounds/<file> -> jsDelivr CDN URL. The car render
    (r5_background / r5_side) follows the R5_CAR_RENDER trim/colour selection."""
    render = _selected_render()

    def repl(m):
        name = m.group(1)
        if render and name in ("r5_background.png", "r5_side.png"):
            return f"{CDN}/{render}"
        path = IMG_MAP.get(name)
        if not path:
            LOG.warning("No CDN mapping for /local/backgrounds/%s — left as-is", name)
            return m.group(0)
        return f"{CDN}/{path}"
    return re.sub(r"/local/backgrounds/([\w.\-]+)", repl, text)


async def _fetch_dashboard(style):
    url = f"{RAW}/{DASHBOARDS[style]}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            raw = await r.text()
    views = yaml.safe_load(_cdnify(raw))
    if not isinstance(views, list):
        raise ValueError("dashboard YAML did not parse to a list of views")
    return {"title": "Renault 5", "views": views}


class _WS:
    """Minimal Home Assistant WebSocket API client over the Supervisor proxy."""

    def __init__(self, session, ws, token):
        self._ws, self._token, self._id = ws, token, 0

    async def _cmd(self, **payload):
        self._id += 1
        payload["id"] = self._id
        await self._ws.send_json(payload)
        while True:
            # Per-receive timeout so an unexpected event/close frame can't hang the deploy.
            msg = await asyncio.wait_for(self._ws.receive_json(), timeout=15)
            if msg.get("id") == self._id and msg.get("type") == "result":
                if not msg.get("success", False):
                    raise RuntimeError(f"{payload['type']} failed: {msg.get('error')}")
                return msg.get("result")

    async def auth(self):
        await self._ws.receive_json()  # auth_required
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        if (await self._ws.receive_json()).get("type") != "auth_ok":
            raise RuntimeError("HA WebSocket auth failed")

    async def dashboards(self):
        return await self._cmd(type="lovelace/dashboards/list")

    async def create_dashboard(self, url_path, title):
        return await self._cmd(type="lovelace/dashboards/create", url_path=url_path,
                               title=title, icon="mdi:car-sports", mode="storage",
                               show_in_sidebar=True, require_admin=False)

    async def save_config(self, url_path, config):
        return await self._cmd(type="lovelace/config/save", url_path=url_path, config=config)

    async def resources(self):
        return await self._cmd(type="lovelace/resources")

    async def create_resource(self, url, res_type="css"):
        return await self._cmd(type="lovelace/resources/create", url=url, res_type=res_type)


def _deploy_targets(style, url_path):
    """(style, url_path, title) to deploy for the chosen deploy_dashboard option. 'both'
    installs the standard dashboard at url_path and the bubble one at '<url_path>-bubble'."""
    if style == "both":
        return [("standard", url_path, "Renault 5"),
                ("bubble", f"{url_path}-bubble", "Renault 5 (Bubble)")]
    return [(style, url_path, "Renault 5")]


async def _deploy_one(api, style, url_path, title, redeploy):
    """Create-once (or overwrite when redeploy) a single dashboard."""
    config = await _fetch_dashboard(style)
    existing = {d.get("url_path") for d in (await api.dashboards() or [])}
    if url_path in existing and not redeploy:
        LOG.info("Dashboard '%s' already exists — leaving it (set redeploy_dashboard to "
                 "overwrite). %d views available.", url_path, len(config["views"]))
        return
    if url_path not in existing:
        await api.create_dashboard(url_path, title)
        LOG.info("Created dashboard '%s'", url_path)
    await api.save_config(url_path, config)
    LOG.info("Deployed '%s' dashboard to '%s' (%d views, CDN assets)",
             style, url_path, len(config["views"]))


# Built-in HA panels / dashboards we must never create-or-overwrite. (A custom dashboard
# url_path must also contain a hyphen, which already excludes most single-word panels —
# this set covers the hyphenated/underscored ones too.)
RESERVED_URL_PATHS = {
    "lovelace", "energy", "map", "logbook", "history", "config", "developer-tools",
    "profile", "todo", "calendar", "media-browser", "default_view", "hassio", "shopping-list",
}


def _validate_url_path(url_path):
    """Return a safe, lowercased dashboard url_path or raise ValueError. Guards against a
    typo'd/hostile value silently overwriting a built-in HA panel via lovelace/config/save."""
    p = url_path.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", p) or "-" not in p:
        raise ValueError(f"invalid dashboard_url_path {url_path!r}: must be lowercase "
                         "letters/digits/'-'/'_', start alphanumeric, and contain a hyphen")
    if p in RESERVED_URL_PATHS:
        raise ValueError(f"dashboard_url_path {url_path!r} is a reserved Home Assistant path")
    return p


async def run_deploy():
    style = os.environ.get("R5_DEPLOY_DASHBOARD", "none").strip().lower()
    if style in ("", "none"):
        return
    if style not in (set(DASHBOARDS) | {"both"}):
        LOG.warning("deploy_dashboard=%r not recognised; skipping", style)
        return
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        LOG.warning("No SUPERVISOR_TOKEN (set homeassistant_api: true); skipping dashboard deploy")
        return
    url_path = os.environ.get("R5_DASHBOARD_URL_PATH", "renault-5").strip() or "renault-5"
    try:
        url_path = _validate_url_path(url_path)
    except ValueError as err:
        LOG.warning("Dashboard auto-deploy skipped — %s", err)
        return
    redeploy = os.environ.get("R5_REDEPLOY_DASHBOARD", "false").strip().lower() in ("true", "1", "on")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect("ws://supervisor/core/websocket",
                                           timeout=aiohttp.ClientTimeout(total=30)) as ws:
                api = _WS(session, ws, token)
                await api.auth()

                # Zen Dots font as a global CSS resource (no font file to copy).
                if not any(r.get("url") == FONT_URL for r in (await api.resources() or [])):
                    await api.create_resource(FONT_URL)
                    LOG.info("Registered Zen Dots font resource")

                for st, path, title in _deploy_targets(style, url_path):
                    await _deploy_one(api, st, path, title, redeploy)
    except Exception as err:  # noqa: BLE001 — deploy must never break the poller
        LOG.warning("Dashboard auto-deploy skipped (%s): %s", type(err).__name__, err)
