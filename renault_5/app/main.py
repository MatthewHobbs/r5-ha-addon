"""Renault 5 add-on — poll the Renault/Kamereon API and publish to HA via MQTT discovery.

A maintained MQTT data layer for the Renault 5 E-Tech, replacing the venv + renault-api
CLI + shell-script layer of Topolino65's renault-5-dashboard-view. Entities follow that
project's naming (modernised, locale-aware). Same renault-api endpoints as the Alpine
A290 add-on this is ported from (R5 E-Tech and A290 share the CMF-BEV / KCM platform).

Read endpoints: battery-status, cockpit, HVAC, location, ev/settings (preconditioning),
ev/soc-levels, plus optional charge-mode and tyre-pressure (gated on supports_endpoint).
Command buttons (ACTION_BUTTONS): Start Charging (KCM instant-charge), Flash Lights, Sound
Horn, and HVAC Start/Stop — all sent natively via renault-api, so **Home Assistant's `renault` integration is not required at all**. Each button is gated on supports_endpoint(), so a
control the platform forbids is never shown (all five are supported on the R5). Refresh
Location is supported too but withheld unless enable_refresh_location is set — on a parked car
it can overwrite the car's recorded position; see the option in DOCS.md. HVAC-start
targets the car's configured preconditioning temperature (falling back to 21°C). One cached
login is reused across polls (VehicleSession) rather than re-authenticating every cycle.
Plug stuck-detection + charge-session tracking + health sensors persist to /data/state.json.

Platform caveats (R5 E-Tech / CMF-BEV, KCM): batteryCapacity is always 0 (we use the
configured capacity); chargingStatus is a float ChargeState (0.0/0.2/1.0/-1.0/… — decoded
via the library enum, not just 0/1/-1); chargingInstantaneousPower units are unreliable;
batteryTemperature is sometimes absent; internalTemperature (cabin temp) is populated but
often only shortly after HVAC activity, so it can be null/unavailable.
"""
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import aiohttp
import catalog
import deploy
from catalog import (
    ACTION_BUTTONS,
    ENV_PREFIX,
    NUMBER_ROLES,
    NUMBERS,
    OBJ_PREFIX,
    OPTIONAL_ENDPOINTS,
    PESSIMISTIC_ENDPOINTS,
    REFRESH_LOCATION_EP,
    SOC_ENDPOINT,
)
from renault_api.exceptions import NotAuthenticatedException
from renault_api.gigya.exceptions import PendingTwoFactorAuthenticationException
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_api.kamereon.exceptions import UnauthorizedException
from renault_api.renault_client import RenaultClient
from renault_mqtt import config, mqtt
from renault_mqtt.charge import CHARGES_ENDPOINT
from renault_mqtt.commands import (
    COMMAND_DEBOUNCE_S,
    command_lock_get,
    dispatch_command,
    hvac_start_action,
    precondition_temp,
)
from renault_mqtt.commands import set_soc_level as shared_set_soc_level
from renault_mqtt.config import _RedactingFilter, cfg, redact
from renault_mqtt.freshness import freshness_fields  # noqa: F401
from renault_mqtt.hvac import hvac_due
from renault_mqtt.parse import (
    _charge_schedule_fields,  # noqa: F401
    _dist,  # noqa: F401
    _enum_label,
    _hvac_schedule_fields,  # noqa: F401
)
from renault_mqtt.plug import detect_plug_suspect as shared_plug_suspect
from renault_mqtt.poll import RHD_LOCALES, poll_vehicle
from renault_mqtt.runtime import (
    HEALTH_PORT,
    MILES_LOCALES,
    panel_page,
    panel_state,
    run_main,
)
from renault_mqtt.runtime import resolve_account as shared_resolve_account
from renault_mqtt.runtime import start_health_server as shared_start_health_server
from renault_mqtt.session import VehicleSession as SharedVehicleSession
from renault_mqtt.support import resolve_support
from renault_mqtt.util import _num, iso, now_ts  # noqa: F401

# Inject this model's env-var prefix into the shared core's redaction net before anything is
# logged, so config.redact / _config_secrets mask this add-on's configured VIN / account_id /
# username / password. Set at import time (the add-on's own suite asserts the wiring).
config.ENV_PREFIX = ENV_PREFIX
# Hand the shared MQTT seam this model's catalog + identity (NODE / DEVICE / topics / discovery
# tables). Must follow the ENV_PREFIX injection above — configure() reads PUBLISH_LOCATION under it.
mqtt.configure(catalog)

LOG = logging.getLogger("renault_5")

STATE_FILE = "/data/state.json"
# Decimal places the published GPS is rounded to before it goes on the retained MQTT topic
# (privacy — coarsens an otherwise full-precision home location). 4 dp ≈ 11 m. Default 4.
# Tolerate the option being absent on an upgraded install (bashio can export "" or "null").
_GPS_P = os.environ.get("R5_GPS_PRECISION", "4").strip()
GPS_PRECISION = max(1, min(6, int(_GPS_P))) if _GPS_P.isdigit() else 4

# Consecutive failures before an optional endpoint is switched off for the session. Small enough
# that log spam is bounded, large enough that a transient blip does not disable a working feature.
BREAKER_TRIP = 3
# Polls to skip before a tripped breaker probes once more. At the default 300s poll that is
# roughly hourly - often enough to notice a server-side fix, rare enough to stay silent.
BREAKER_RETRY_EVERY = 12
_BREAKER_FAILS = {}
_BREAKERS = {}
_BREAKER_SKIPS = {}

VERSION = os.environ.get("R5_VERSION", "dev")  # injected via the Dockerfile (BUILD_VERSION)

# Friendly labels for the ChargeState/PlugState enums (every member mapped, so float
# sub-states never surface raw). Dashboards key on "Charging"/"Connected"/"Disconnected".
CHARGE_STATUS_LABELS = {
    ChargeState.NOT_IN_CHARGE: "Not Charging",
    ChargeState.WAITING_FOR_A_PLANNED_CHARGE: "Waiting (Planned)",
    ChargeState.CHARGE_ENDED: "Charge Ended",
    ChargeState.WAITING_FOR_CURRENT_CHARGE: "Waiting to Charge",
    ChargeState.ENERGY_FLAP_OPENED: "Flap Open",
    ChargeState.CHARGE_IN_PROGRESS: "Charging",
    ChargeState.CHARGE_ERROR: "Error",
    ChargeState.UNAVAILABLE: "Unavailable",
    ChargeState.V2G_CHARGING_WAITING: "V2G Waiting",
    ChargeState.V2L_CONNECTED: "V2L Connected",
    ChargeState.V2G_DISCHARGING: "V2G Discharging",
    ChargeState.V2G_CHARGING_NORMAL: "V2G Charging",
}
PLUG_STATUS_LABELS = {
    PlugState.UNPLUGGED: "Disconnected",
    PlugState.PLUGGED: "Connected",
    PlugState.PLUG_ERROR: "Plug Error",
    PlugState.PLUG_UNKNOWN: "Unknown",
}
# plug stuck-detection thresholds (mirrors the original template logic)
PLUG_KM_DELTA = 1       # km driven since baseline => evidence of "actually unplugged"
PLUG_SOC_DROP = 2       # %SoC dropped since baseline
PLUG_MIN_AGE = 600      # ignore baselines younger than 10 min
PLUG_MAX_AGE = 12 * 3600  # ...or older than 12 h


def setup_logging():
    level = getattr(logging, cfg("R5_LOG_LEVEL", "info").upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    # Attach the secret-redaction net to the root handler(s): every record (ours + the
    # library's, which propagates to root) is scrubbed before it's emitted.
    redactor = _RedactingFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redactor)
    # The library's DEBUG logs full Kamereon response bodies (unrounded GPS, account data);
    # debug_dump is the redacted way to see them.
    for noisy in ("renault_api", "renault_api.kamereon", "renault_api.gigya"):
        logging.getLogger(noisy).setLevel(max(level, logging.INFO))


def load_state():
    try:
        with open(STATE_FILE) as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    # A restored or hand-edited file can hold valid JSON of the wrong shape; the
    # poll's own error path then calls .get() and would die inside its handler.
    return data if isinstance(data, dict) else {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_FILE)   # atomic: a kill mid-write never corrupts the state file
    except OSError as err:
        LOG.warning("Could not persist state: %s", err)


def charging_status_label(battery):
    return _enum_label(battery.get_charging_status(), CHARGE_STATUS_LABELS,
                       getattr(battery, "chargingStatus", None))


def is_charging(battery):
    power = _num(getattr(battery, "chargingInstantaneousPower", None)) or 0
    return battery.get_charging_status() == ChargeState.CHARGE_IN_PROGRESS or power > 0.1


async def _login_vehicle(websession, locale):
    client = RenaultClient(websession=websession, locale=locale)
    await client.session.login(cfg("R5_USERNAME"), cfg("R5_PASSWORD"))
    account = await client.get_api_account(await resolve_account(client))
    return await account.get_api_vehicle(cfg("R5_VIN"))


# Per request. aiohttp's default lets one hung Gigya/Kamereon call run 5 minutes, and a login is
# several calls; renault-api sets none of its own. Startup and commands have no outer wait_for.
API_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)


class VehicleSession(SharedVehicleSession):
    """Poll-owned lifecycle with app transport, locale, login and logging adapters."""

    def __init__(self, locale):
        self.locale = locale
        super().__init__(
            session_factory=lambda: aiohttp.ClientSession(timeout=API_TIMEOUT),
            login=lambda ws: _login_vehicle(ws, self.locale),
        )

    async def vehicle(self):
        cached = self._vehicle is not None
        vehicle = await super().vehicle()
        if not cached:
            LOG.info("Logged in to the Renault API (session cached for reuse)")
        return vehicle


async def _supports(vehicle, ep):
    """supports_endpoint() is async in the pinned renault-api; await it."""
    return await vehicle.supports_endpoint(ep)


SUPPORT_ENDPOINTS = (set(OPTIONAL_ENDPOINTS) | {ep for _name, _icon, ep in ACTION_BUTTONS.values()}
                     | {SOC_ENDPOINT, CHARGES_ENDPOINT})


async def detect_supported(vsession, supported=None, unresolved=None):
    """Return supported endpoints, resolving only unknown probes when retrying.

    Passed sets are updated together after each definite answer, so a later timeout cannot
    forget a successful probe. Exceptions preserve prior support and leave the probe pending.
    """
    # Unknown data endpoints stay optimistic except hvac-settings; unknown controls stay hidden.
    if supported is None:
        supported = set(OPTIONAL_ENDPOINTS) - PESSIMISTIC_ENDPOINTS
    if unresolved is None:
        unresolved = set(SUPPORT_ENDPOINTS)
    try:
        vehicle = await vsession.vehicle()
        await resolve_support(
            lambda ep: _supports(vehicle, ep), supported, unresolved,
            lambda ep, err: LOG.warning("supports_endpoint(%s) check failed: %s", ep, err),
        )
        LOG.info("Supported optional endpoints: %s", sorted(supported))
    except Exception as err:  # noqa: BLE001
        await vsession.invalidate()
        LOG.warning("Endpoint-support detection failed (keeping prior support): %s", redact(err))
    return supported


def _precondition_temp():
    """Target cabin temperature (°C) for Start Climate, or None to use the car's own setting.

    Unset on the Configuration page means the car decides: its preconditioning temperature is
    the one its own schedule uses, and it is what the dashboard shows as "Desired Temp"."""
    return precondition_temp("R5_PRECONDITION_TEMPERATURE")


async def _hvac_start_action(vehicle):
    """Start Climate at the configured temperature, or at the car's own when none is set."""
    await hvac_start_action(vehicle, get_temp=_precondition_temp)


# Command-topic suffix -> coroutine taking the logged-in vehicle. Keys match ACTION_BUTTONS.
COMMAND_ACTIONS = {
    "charge_start": lambda v: v.set_charge_start(),
    "horn":         lambda v: v.start_horn(),
    "lights":       lambda v: v.start_lights(),
    "hvac_start":   _hvac_start_action,
    "hvac_stop":    lambda v: v.set_ac_stop(),
    "refresh_location": lambda v: v.refresh_location(),
}


_last_command = {}
# Commands still logging in or executing. A login can take the whole 60s API timeout, so the 5s
# window alone would let a repeat through while the first press's outcome is still unknown.
_in_flight = set()


def _debounce_now():
    """The debounce's clock. Monotonic, not wall time: an NTP or manual correction that moves the
    wall clock back would otherwise hold a button suppressed for the size of the jump."""
    return time.monotonic()


# Command suffixes that trigger a location refresh, refused unless the user opted in and location
# publishing is on (publish_discovery clears the button in the same cases). Gating the command
# matters: the entity is pressable from voice, automations and dashboards.
LOCATION_CMDS = {oid.removeprefix(OBJ_PREFIX) for oid, vals in ACTION_BUTTONS.items()
                 if vals[-1] == REFRESH_LOCATION_EP}

# Command-topic suffixes that map to writable numbers, and each one's set_battery_soc role.
NUMBER_CMDS = {oid.removeprefix(OBJ_PREFIX) for oid in NUMBERS}
_NUMBER_ROLE = {oid.removeprefix(OBJ_PREFIX): role for oid, role in NUMBER_ROLES.items()}
# The sliders' (min, max, step), enforced on the command too: any MQTT client can publish to the topic.
NUMBER_RANGES = {oid.removeprefix(OBJ_PREFIX): (lo, hi, step) for oid, (_n, _i, lo, hi, step) in NUMBERS.items()}


def _command_lock_get(group):
    """Separate climate and charge-limit locks, recreated when the event loop changes."""
    return command_lock_get(group)


async def set_soc_level(which, payload):
    """Write a charge limit. set_battery_soc needs both min and target together, so the
    opposing slider's current value is read back first and re-sent unchanged. Serialised so
    adjusting both sliders in quick succession can't interleave and clobber a change."""
    locale = cfg("R5_LOCALE", "en_GB")

    @asynccontextmanager
    async def acquire():
        async with aiohttp.ClientSession(timeout=API_TIMEOUT) as websession:
            yield await _login_vehicle(websession, locale)

    await shared_set_soc_level(
        which, payload, number_ranges=NUMBER_RANGES,
        acquire_vehicle=acquire, log=LOG, number_roles=_NUMBER_ROLE, lock_get=_command_lock_get,
    )


async def run_command(cmd, payload=""):
    """Dispatch an MQTT command: a button press to its renault-api action, or a number set
    to set_battery_soc. Never fatal."""
    locale = cfg("R5_LOCALE", "en_GB")

    @asynccontextmanager
    async def acquire():
        async with aiohttp.ClientSession(timeout=API_TIMEOUT) as websession:
            yield await _login_vehicle(websession, locale)

    await dispatch_command(
        cmd, payload, number_cmds=NUMBER_CMDS, set_soc=set_soc_level,
        location_cmds=LOCATION_CMDS, publish_location=mqtt.PUBLISH_LOCATION,
        enable_refresh_location=mqtt.ENABLE_REFRESH_LOCATION, actions=COMMAND_ACTIONS,
        last_command=_last_command, in_flight=_in_flight,
        acquire_vehicle=acquire, log=LOG,
        climate_cmds=frozenset({"hvac_start", "hvac_stop"}),
        debounce_s=COMMAND_DEBOUNCE_S, now=_debounce_now, lock_get=_command_lock_get,
    )


def detect_plug_suspect(state, plug, mileage, soc, charging):
    """Interpret Renault plug reports and apply this model's suspicion policy."""
    return shared_plug_suspect(
        state, plugged=plug == PlugState.PLUGGED, unplugged=plug == PlugState.UNPLUGGED,
        mileage=mileage, soc=soc, charging=charging, now=now_ts(),
        min_age=PLUG_MIN_AGE, max_age=PLUG_MAX_AGE, km_delta=PLUG_KM_DELTA, soc_drop=PLUG_SOC_DROP)


async def resolve_account(client):
    """Resolve the Kamereon account id for this Renault 5."""
    return await shared_resolve_account(client, log=LOG, prefix="R5_", cfg_fn=cfg)


def _hvac_due(supported_eps):
    """Should this poll call hvac-settings? Yes when the car lists it and the breaker is clear;
    yes once every BREAKER_RETRY_EVERY polls while tripped, so a server-side recovery is noticed
    without a restart (the reset lives inside the call, so a breaker that never retried could
    never clear itself); no otherwise."""
    return hvac_due(supported_eps, _BREAKERS, _BREAKER_SKIPS, retry_every=BREAKER_RETRY_EVERY)


_POLL_KEYS = {
    "range": "battery_autonomy",
    "charging_power": "charging_rate",
    "charging_remaining": "charging_remaining_time",
    "plug_status": "charger_plug_status",
    "charging_flap": "charging_flap_status",
    "charging_status": "charger_status",
    "last_updated": "battery_last_activity",
    "mileage": "vehicle_mileage",
    "soc_target": "soc_max_target",
    "soc_min": "soc_min_target",
}


async def poll_once(vsession, state, capacity_kwh, supported_eps, dist_unit):
    """Assemble one Renault 5 poll snapshot and optional coarsened location attributes."""
    return await poll_vehicle(
        vsession, state, capacity_kwh, supported_eps, dist_unit,
        plug_status_labels=PLUG_STATUS_LABELS, plug_plugged=PlugState.PLUGGED,
        charging_check=is_charging, charging_label=charging_status_label,
        breakers=_BREAKERS, breaker_fails=_BREAKER_FAILS, breaker_skips=_BREAKER_SKIPS,
        breaker_trip=BREAKER_TRIP, breaker_retry_every=BREAKER_RETRY_EVERY,
        hvac_due=_hvac_due, gps_precision=GPS_PRECISION,
        latest_data=lambda: _LATEST.get("data"), detect_plug_suspect=detect_plug_suspect,
        log=LOG, keys=_POLL_KEYS, cabin_temp=True, rhd_locales=RHD_LOCALES,
    )


# Latest poll snapshot for the read-only ingress status panel. Deliberately excludes raw
# GPS (lat/lon are published separately to MQTT, not stored here) and any credential.
_LATEST = {"version": VERSION, "ok": False, "last_poll": None, "supported": [],
           "dist_unit": "km", "data": {}}

_PANEL_FILE = os.path.join(os.path.dirname(__file__), "panel.html")
_AUTH_EXCEPTIONS = (
    NotAuthenticatedException,
    UnauthorizedException,
    PendingTwoFactorAuthenticationException,
)


async def _panel_page(_req):
    """Serve the self-contained, read-only ingress status panel."""
    return await panel_page(_PANEL_FILE)


async def _panel_state(_req):
    """JSON the panel polls — latest car state + diagnostics; no credentials, no raw GPS."""
    return await panel_state(_LATEST)


async def start_health_server(health_state=None):
    """/healthz (backs the Dockerfile HEALTHCHECK) plus the read-only ingress status panel
    (GET / and GET /api/state) on the poll loop. The shared server answers 503 once the loop
    misses its expected-progress deadline, so a stalled container is marked unhealthy and
    restarted; a deadlocked loop cannot answer at all."""
    return await shared_start_health_server(
        panel_file=_PANEL_FILE, latest=_LATEST, log=LOG, port=HEALTH_PORT,
        panel_page_fn=_panel_page, panel_state_fn=_panel_state, health_state=health_state,
    )


async def main():
    await run_main(
        addon_name="Renault 5",
        version=VERSION,
        setup_logging=setup_logging,
        run_command=run_command,
        start_health_server=start_health_server,
        load_state=load_state,
        save_state=save_state,
        latest=_LATEST,
        vsession_factory=VehicleSession,
        support_endpoints=SUPPORT_ENDPOINTS,
        initial_supported=set(OPTIONAL_ENDPOINTS) - PESSIMISTIC_ENDPOINTS,
        detect_supported=detect_supported,
        run_deploy=deploy.run_deploy,
        poll_once=poll_once,
        api_timeout_total=API_TIMEOUT.total,
        log=LOG,
        prefix="R5_",
        miles_locales=MILES_LOCALES,
        plug_key="charger_plug_status",
        last_updated_key="battery_last_activity",
        log_duration=True,
        early_signals=True,
        auth_exceptions=_AUTH_EXCEPTIONS,
        http_error_type=aiohttp.ClientResponseError,
        now=now_ts,
        monotonic=time.monotonic,
        event_factory=asyncio.Event,
        wait_for=asyncio.wait_for,
        cfg_fn=cfg,
    )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
