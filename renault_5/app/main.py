"""Renault 5 add-on — poll the Renault/Kamereon API and publish to HA via MQTT discovery.

A maintained MQTT data layer for the Renault 5 E-Tech, replacing the venv + renault-api
CLI + shell-script layer of Topolino65's renault-5-dashboard-view. Entities follow that
project's naming (modernised, locale-aware). Same renault-api endpoints as the Alpine
A290 add-on this is ported from (R5 E-Tech and A290 share the CMF-BEV / KCM platform).

Read endpoints: battery-status, cockpit, HVAC, location, ev/settings (preconditioning),
ev/soc-levels, plus optional charge-mode and tyre-pressure (gated on supports_endpoint).
Command buttons (ACTION_BUTTONS): Start Charging (KCM instant-charge), Flash Lights, Sound
Horn, and HVAC Start/Stop — all sent natively via renault-api, so **Home Assistant's `renault` integration is not required at all**. Each button is gated on supports_endpoint(), so a
control the platform forbids is never shown (all five are supported on the R5). HVAC-start
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
import inspect
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

import aiohttp
import deploy
import paho.mqtt.client as mqtt
from aiohttp import web
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_api.renault_client import RenaultClient

LOG = logging.getLogger("renault_5")

DISCOVERY_PREFIX = "homeassistant"
NODE = "renault_5"
STATE_TOPIC = f"{NODE}/state"
ATTR_TOPIC = f"{NODE}/location/attributes"
TRACKER_STATE_TOPIC = f"{NODE}/location/state"
AVAIL_TOPIC = f"{NODE}/availability"
CMD_PREFIX = f"{NODE}/cmd/"          # button commands: renault_5/cmd/<suffix>
STATE_FILE = os.environ.get("R5_STATE_FILE", "/data/state.json")

# Device name "R5" is deliberate: HA builds entity_ids from slug(device + entity name) and
# ignores object_id, so this yields sensor.r5_<name> (what the dashboards expect).
DEVICE = {"identifiers": [NODE], "name": "R5", "manufacturer": "Renault", "model": "R5 E-Tech"}
VERSION = "0.11.1"

_LOOP = None  # asyncio loop, set in main(), used to bridge paho callbacks

# object_id -> (name, device_class, unit, state_class). Object_ids follow the Topolino
# R5 naming (minus the legacy _api/_mi suffixes); units are locale-aware (see publish).
SENSORS = {
    "r5_battery_level":          ("Battery Level", "battery", "%", "measurement"),
    "r5_battery_autonomy":       ("Battery Autonomy", "distance", "km", "measurement"),
    "r5_battery_temperature":    ("Battery Temperature", "temperature", "°C", "measurement"),
    "r5_charging_rate":          ("Charging Rate", "power", "kW", "measurement"),
    "r5_charging_remaining_time": ("Charging Remaining Time", "duration", "min", "measurement"),
    "r5_available_energy":       ("Available Energy", "energy_storage", "kWh", "measurement"),
    "r5_charger_plug_status":    ("Charger Plug Status", None, None, None),
    "r5_charger_status":         ("Charger Status", None, None, None),
    "r5_charging_flap_status":   ("Charging Flap Status", None, None, None),
    "r5_drive_side":             ("Drive Side", None, None, None),
    "r5_vehicle_mileage":        ("Vehicle Mileage", "distance", "km", "total_increasing"),
    "r5_preconditioning_temperature": ("Preconditioning Temperature", "temperature", "°C", None),
    "r5_hvac_last_activity":     ("HVAC Last Activity", "timestamp", None, None),
    "r5_gps_last_activity":      ("GPS Last Activity", "timestamp", None, None),
    "r5_external_temperature":   ("Outside Temperature", "temperature", "°C", "measurement"),
    "r5_cabin_temperature":      ("Cabin Temperature", "temperature", "°C", "measurement"),
    "r5_hvac_status":            ("HVAC Status", None, None, None),
    "r5_hvac_soc_threshold":     ("HVAC SoC Threshold", "battery", "%", None),
    "r5_charge_mode":            ("Charge Mode", None, None, None),
    "r5_tyre_pressure_fl":       ("Tyre Pressure Front Left", None, None, "measurement"),
    "r5_tyre_pressure_fr":       ("Tyre Pressure Front Right", None, None, "measurement"),
    "r5_tyre_pressure_rl":       ("Tyre Pressure Rear Left", None, None, "measurement"),
    "r5_tyre_pressure_rr":       ("Tyre Pressure Rear Right", None, None, "measurement"),
    "r5_battery_last_activity":  ("Battery Last Activity", "timestamp", None, None),
    "r5_last_charge_start":          ("Last Charge Start", "timestamp", None, None),
    "r5_last_charge_end":            ("Last Charge End", "timestamp", None, None),
    "r5_last_charge_start_soc":      ("Last Charge Start SoC", "battery", "%", None),
    "r5_last_charge_end_soc":        ("Last Charge End SoC", "battery", "%", None),
    "r5_last_charge_start_energy":   ("Last Charge Start Energy", "energy", "kWh", None),
    "r5_last_charge_end_energy":     ("Last Charge End Energy", "energy", "kWh", None),
    "r5_last_charge_recovered_pct":  ("Last Charge SoC Recovered", None, "%", None),
    "r5_last_charge_recovered_kwh":  ("Last Charge Energy Recovered", "energy", "kWh", None),
    "r5_last_charge_duration_min":   ("Last Charge Duration", "duration", "min", None),
    "r5_last_charge_average_power":  ("Last Charge Average Power", "power", "kW", None),
    "r5_last_charge_type":           ("Last Charge Type", None, None, None),
}
# object_id -> (name, device_class)
BINARY_SENSORS = {
    "r5_charging":              ("Charging", "battery_charging"),
    "r5_heated_steering_wheel": ("Heated Steering Wheel", None),
    "r5_heated_seat_driver":    ("Heated Seat Driver", None),
    "r5_heated_seat_passenger": ("Heated Seat Passenger", None),
    "r5_plug_suspect":          ("Plug State Suspect", "problem"),
    "r5_api_auth_failure":      ("API Auth Failure", "problem"),
    "r5_data_stale":            ("Data Stale", "problem"),
}

# Icons for text/status sensors that would otherwise fall back to HA's generic mdi:eye.
ICONS = {
    "r5_charger_plug_status":   "mdi:power-plug",
    "r5_charger_status":        "mdi:battery-charging",
    "r5_charging_flap_status":  "mdi:ev-plug-type2",
    "r5_drive_side":            "mdi:steering",
    "r5_hvac_status":           "mdi:fan",
    "r5_charge_mode":           "mdi:ev-station",
    "r5_last_charge_type":      "mdi:ev-station",
    "r5_heated_steering_wheel": "mdi:steering",
    "r5_heated_seat_driver":    "mdi:car-seat-heater",
    "r5_heated_seat_passenger": "mdi:car-seat-heater",
}

# Optional endpoints some models don't expose — gated on supports_endpoint().
OPTIONAL_ENDPOINTS = {
    "charge-mode": ["r5_charge_mode"],
    "pressure": ["r5_tyre_pressure_fl", "r5_tyre_pressure_fr",
                 "r5_tyre_pressure_rl", "r5_tyre_pressure_rr"],
}

# suffix (renault_5/cmd/<key>) -> (object_id, node-segment, name, icon, action endpoint).
# Published only when supports_endpoint() is true, so a forbidden control is never shown.
ACTION_BUTTONS = {
    "charge_start": ("r5_charge_start", "charge_start", "Start Charging", "mdi:ev-station", "actions/charge-start"),
    "lights":       ("r5_flash_lights", "flash_lights", "Flash Lights", "mdi:car-light-high", "actions/lights-start"),
    "horn":         ("r5_sound_horn", "sound_horn", "Sound Horn", "mdi:bullhorn", "actions/horn-start"),
    "hvac_start":   ("r5_start_air_conditioner", "start_air_conditioner", "Start Air Conditioner", "mdi:air-conditioner", "actions/hvac-start"),
    "hvac_stop":    ("r5_stop_air_conditioner", "stop_air_conditioner", "Stop Air Conditioner", "mdi:fan-off", "actions/hvac-stop"),
    "refresh_location": ("r5_refresh_location", "refresh_location", "Refresh Location", "mdi:crosshairs-gps", "actions/refresh-location"),
}

# Writable charge-limit controls. State comes from the poll's soc-levels read (data key =
# object_id without the r5_ prefix); a slider move writes via set_battery_soc(). Gated on
# SOC_ENDPOINT, so a model that rejects the write never ships the control.
# object_id -> (name, icon, role, min, max, step); role ("min"/"target") selects the arg.
SOC_ENDPOINT = "soc-levels"
NUMBERS = {
    "r5_soc_min_target": ("SOC Min Target", "mdi:battery-arrow-down", "min",    15, 45,  5),
    "r5_soc_max_target": ("SOC Max Target", "mdi:battery-arrow-up",   "target", 55, 100, 5),
}

# Sensor object_ids a previous version published but no longer ships. Their retained
# discovery config is cleared on startup so upgraded installs don't keep a dead entity.
# soc_*_target moved from SENSORS to NUMBERS, so clear their old sensor configs.
RETIRED_SENSORS = ["r5_soc_max_target", "r5_soc_min_target"]

# Published but disabled in the entity registry by default — mapping artifacts with no
# user-meaningful state. drive_side is just RHD/LHD derived from locale (used internally for
# heated-seat mapping); it adds noise to the entity list. Users who want it can re-enable it.
DEFAULT_DISABLED_SENSORS = {"r5_drive_side"}

HOME_POWER_MAX_KW = 7.4
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
# Drive side from locale (UK + Ireland are RHD; the rest of the supported markets LHD).
# The dashboard uses this to map the API's left/right seats to driver/passenger.
RHD_LOCALES = {"en_gb", "en_ie"}
# Distance units: only the UK uses miles; everywhere else (incl. Ireland) uses km.
MILES_LOCALES = {"en_gb"}
# plug stuck-detection thresholds (mirrors the original template logic)
PLUG_KM_DELTA = 1       # km driven since baseline => evidence of "actually unplugged"
PLUG_SOC_DROP = 2       # %SoC dropped since baseline
PLUG_MIN_AGE = 600      # ignore baselines younger than 10 min
PLUG_MAX_AGE = 12 * 3600  # ...or older than 12 h


def cfg(name, default=""):
    return os.environ.get(name, default)


def setup_logging():
    level = cfg("R5_LOG_LEVEL", "info").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s")


def now_ts():
    return time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_FILE)   # atomic: a kill mid-write never corrupts the state file
    except OSError as err:
        LOG.warning("Could not persist state: %s", err)


def _on_message(client, userdata, msg):
    if _LOOP is not None and msg.topic.startswith(CMD_PREFIX):
        cmd = msg.topic[len(CMD_PREFIX):]
        payload = msg.payload.decode(errors="replace") if msg.payload else ""
        LOG.info("Received command: %s %s", cmd, payload)
        asyncio.run_coroutine_threadsafe(run_command(cmd, payload), _LOOP)


def _on_connect(client, userdata, flags, reason_code, properties=None):
    # Runs on the initial connect *and* every reconnect: re-subscribe + re-announce online.
    if reason_code == 0:
        LOG.info("MQTT connected")
        client.subscribe(f"{CMD_PREFIX}#")
        client.publish(AVAIL_TOPIC, "online", retain=True)
    else:
        LOG.warning("MQTT connect refused: %s", reason_code)


def _on_disconnect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        LOG.warning("MQTT disconnected (%s) — reconnecting", reason_code)


def mqtt_connect():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="renault_5_addon")
    if cfg("MQTT_USER"):
        client.username_pw_set(cfg("MQTT_USER"), cfg("MQTT_PASS"))
    client.will_set(AVAIL_TOPIC, "offline", retain=True)
    client.on_message = _on_message
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=120)   # bounded backoff on broker drop
    LOG.info("Connecting to MQTT %s:%s", cfg("MQTT_HOST"), cfg("MQTT_PORT", "1883"))
    client.connect(cfg("MQTT_HOST"), int(cfg("MQTT_PORT", "1883") or "1883"), keepalive=30)
    client.loop_start()
    return client


def publish_discovery(client, supported_eps, dist_unit):
    skip = {obj for ep, objs in OPTIONAL_ENDPOINTS.items()
            if ep not in supported_eps for obj in objs}
    # Clear discovery for unsupported + retired entities (removes ones a previous
    # version may have published with retain=True).
    for obj in set(skip) | set(RETIRED_SENSORS):
        client.publish(f"{DISCOVERY_PREFIX}/sensor/{NODE}/{obj}/config", "", retain=True)
    published = 0
    for obj, (name, dev_class, unit, state_class) in SENSORS.items():
        if obj in skip:
            continue
        published += 1
        if obj in ("r5_battery_autonomy", "r5_vehicle_mileage"):
            unit = dist_unit   # locale-aware (mi for UK, km elsewhere)
            if dist_unit == "mi":
                dev_class = None  # else HA (metric) re-converts our miles back to km
        conf = {"name": name, "object_id": obj, "unique_id": obj,
                "state_topic": STATE_TOPIC, "value_template": "{{ value_json.%s }}" % obj.removeprefix("r5_"),
                "availability_topic": AVAIL_TOPIC, "device": DEVICE}
        if dev_class:
            conf["device_class"] = dev_class
        if unit:
            conf["unit_of_measurement"] = unit
        if state_class:
            conf["state_class"] = state_class
        if obj in ICONS:
            conf["icon"] = ICONS[obj]
        if obj in DEFAULT_DISABLED_SENSORS:
            conf["enabled_by_default"] = False
        client.publish(f"{DISCOVERY_PREFIX}/sensor/{NODE}/{obj}/config", json.dumps(conf), retain=True)
    for obj, (name, dev_class) in BINARY_SENSORS.items():
        conf = {"name": name, "object_id": obj, "unique_id": obj,
                "state_topic": STATE_TOPIC, "value_template": "{{ value_json.%s }}" % obj.removeprefix("r5_"),
                "payload_on": "on", "payload_off": "off",
                "availability_topic": AVAIL_TOPIC, "device": DEVICE}
        if dev_class:
            conf["device_class"] = dev_class
        if obj in ICONS:
            conf["icon"] = ICONS[obj]
        client.publish(f"{DISCOVERY_PREFIX}/binary_sensor/{NODE}/{obj}/config", json.dumps(conf), retain=True)
    tracker = {"name": "Location", "object_id": "r5_car_location", "unique_id": "r5_car_location",
               "state_topic": TRACKER_STATE_TOPIC, "json_attributes_topic": ATTR_TOPIC,
               "availability_topic": AVAIL_TOPIC, "source_type": "gps", "device": DEVICE}
    client.publish(f"{DISCOVERY_PREFIX}/device_tracker/{NODE}/location/config", json.dumps(tracker), retain=True)
    # Buttons gated on supports_endpoint(): published when supported, else cleared.
    shipped = []
    for cmd_suffix, (oid, node, name, icon, ep) in ACTION_BUTTONS.items():
        topic = f"{DISCOVERY_PREFIX}/button/{NODE}/{node}/config"
        if ep in supported_eps:
            conf = {"name": name, "object_id": oid, "unique_id": oid,
                    "command_topic": f"{CMD_PREFIX}{cmd_suffix}", "availability_topic": AVAIL_TOPIC,
                    "icon": icon, "device": DEVICE}
            client.publish(topic, json.dumps(conf), retain=True)
            shipped.append(node)
        else:
            client.publish(topic, "", retain=True)
    # Writable charge-limit numbers, gated on soc-levels support (else cleared).
    numbers = []
    soc_ok = SOC_ENDPOINT in supported_eps
    for obj, (name, icon, _role, mn, mx, step) in NUMBERS.items():
        short = obj.removeprefix("r5_")
        topic = f"{DISCOVERY_PREFIX}/number/{NODE}/{short}/config"
        if soc_ok:
            conf = {"name": name, "object_id": obj, "unique_id": obj,
                    "state_topic": STATE_TOPIC, "value_template": "{{ value_json.%s }}" % short,
                    "command_topic": f"{CMD_PREFIX}{short}", "availability_topic": AVAIL_TOPIC,
                    "min": mn, "max": mx, "step": step, "mode": "slider",
                    "unit_of_measurement": "%", "device_class": "battery",
                    "optimistic": True, "icon": icon, "device": DEVICE}
            client.publish(topic, json.dumps(conf), retain=True)
            numbers.append(short)
        else:
            client.publish(topic, "", retain=True)
    LOG.info("Published discovery: %d sensors (%d unsupported cleared), %d binary_sensors, "
             "device_tracker, buttons=%s, numbers=%s",
             published, len(skip), len(BINARY_SENSORS), shipped or "none", numbers or "none")


KM_TO_MI = 0.621371


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _mi(km):
    v = _num(km)
    return round(v * KM_TO_MI, 1) if v is not None else None


def _dist(km, unit):
    """Convert a km value to the locale unit ('mi' or 'km')."""
    return _mi(km) if unit == "mi" else _num(km)


def _bool_on(v):
    return "on" if v in (True, "true", "True", "on", "ON", 1, "1") else "off"


def _find_precond(obj, _depth=0):
    """Locate the dict holding preconditioning* fields in the ev/settings payload,
    regardless of how the kcm response nests it."""
    if not isinstance(obj, dict) or _depth > 4:
        return {}
    if any(k.startswith("preconditioning") for k in obj):
        return obj
    for key in ("attributes", "data", "ev"):
        found = _find_precond(obj.get(key), _depth + 1)
        if found:
            return found
    return {}


def _enum_label(enum_val, labels, raw):
    """Friendly label for a decoded enum; fall back to a prettified name, then raw."""
    if enum_val is not None:
        return labels.get(enum_val, enum_val.name.replace("_", " ").title())
    return "Unknown" if raw is None else f"Unknown ({raw})"


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


class VehicleSession:
    """One logged-in renault-api vehicle + its aiohttp session, reused across polls.

    Earlier versions logged in (full Gigya auth) on *every* poll — ~288 logins/day at the
    default 300 s interval, which risks Renault-side throttling. Here the websession and
    vehicle are created once and reused; renault-api refreshes its own access tokens as
    they expire. ``invalidate()`` drops the cached login (closing the socket) so the next
    ``vehicle()`` re-authenticates — the poll loop calls it after any failed poll so a
    stale token or a dropped connection always self-heals on the following cycle.

    Owned solely by the poll loop (detect_supported + poll_once). Button presses keep their
    own short-lived login in run_command, so there's no concurrent use of this session.
    """

    def __init__(self, locale):
        self.locale = locale
        self._websession = None
        self._vehicle = None

    async def vehicle(self):
        if self._vehicle is None:
            self._websession = aiohttp.ClientSession()
            try:
                self._vehicle = await _login_vehicle(self._websession, self.locale)
            except Exception:
                await self.invalidate()   # never leak a half-open session
                raise
            LOG.info("Logged in to the Renault API (session cached for reuse)")
        return self._vehicle

    async def invalidate(self):
        """Drop the cached login so the next vehicle() re-authenticates."""
        if self._websession is not None:
            try:
                await self._websession.close()
            except Exception:  # noqa: BLE001
                pass
        self._websession = None
        self._vehicle = None

    async def close(self):
        """Release the session at shutdown."""
        await self.invalidate()


async def _supports(vehicle, ep):
    """supports_endpoint() is async in renault-api 0.5.x; tolerate a sync return too."""
    res = vehicle.supports_endpoint(ep)
    return (await res) if inspect.isawaitable(res) else res


async def detect_supported(vsession):
    """Probe which optional endpoints this car exposes (reusing the cached login).

    Returns a set of endpoint names. The GET data endpoints (OPTIONAL_ENDPOINTS) default
    to supported if detection fails — they're read-only and harmless if empty. The action
    endpoints behind ACTION_BUTTONS default to *unsupported* so a platform that forbids one
    never gets a dead control button.
    """
    supported = set(OPTIONAL_ENDPOINTS)
    action_eps = {ep for _oid, _node, _name, _icon, ep in ACTION_BUTTONS.values()}
    try:
        vehicle = await vsession.vehicle()
        for ep in list(OPTIONAL_ENDPOINTS):
            try:
                if not await _supports(vehicle, ep):
                    supported.discard(ep)
            except Exception as err:  # noqa: BLE001
                LOG.warning("supports_endpoint(%s) check failed: %s", ep, err)
        for ep in sorted(action_eps | {SOC_ENDPOINT}):
            try:
                if await _supports(vehicle, ep):
                    supported.add(ep)
            except Exception as err:  # noqa: BLE001
                LOG.warning("supports_endpoint(%s) check failed: %s", ep, err)
        LOG.info("Supported optional endpoints: %s", sorted(supported))
    except Exception as err:  # noqa: BLE001
        await vsession.invalidate()   # don't keep a half-broken login
        LOG.warning("Endpoint-support detection failed (publishing sensors, hiding action buttons): %s", err)
    return supported


async def _hvac_start_action(vehicle):
    """set_ac_start needs a target temp; use the car's configured preconditioning
    temperature (what the dashboard shows as "Desired Temp"), falling back to 21°C."""
    temp = 21.0
    try:
        p = _find_precond(await vehicle.get_charge_schedule())
        temp = float(p.get("preconditioningTemperature") or 21)
    except Exception:  # noqa: BLE001
        pass
    await vehicle.set_ac_start(temp)


# Command-topic suffix -> coroutine taking the logged-in vehicle. Keys match ACTION_BUTTONS.
COMMAND_ACTIONS = {
    "charge_start": lambda v: v.set_charge_start(),
    "horn":         lambda v: v.start_horn(),
    "lights":       lambda v: v.start_lights(),
    "hvac_start":   _hvac_start_action,
    "hvac_stop":    lambda v: v.set_ac_stop(),
    "refresh_location": lambda v: v.refresh_location(),
}


COMMAND_DEBOUNCE_S = 5
_last_command = {}

# Command-topic suffixes that map to writable numbers, and each one's set_battery_soc role.
NUMBER_CMDS = {oid.removeprefix("r5_") for oid in NUMBERS}
_NUMBER_ROLE = {oid.removeprefix("r5_"): role
                for oid, (_n, _i, role, _mn, _mx, _s) in NUMBERS.items()}

_soc_lock = None
_soc_lock_loop = None


def _soc_lock_get():
    """One lock per event loop, serialising charge-limit writes so two quick slider moves
    can't interleave a read-modify-write and clobber each other. Re-created if the running
    loop changes (so per-test event loops don't trip cross-loop binding)."""
    global _soc_lock, _soc_lock_loop
    loop = asyncio.get_running_loop()
    if _soc_lock is None or _soc_lock_loop is not loop:
        _soc_lock, _soc_lock_loop = asyncio.Lock(), loop
    return _soc_lock


async def set_soc_level(which, payload):
    """Write a charge limit. set_battery_soc needs both min and target together, so the
    opposing slider's current value is read back first and re-sent unchanged. Serialised so
    adjusting both sliders in quick succession can't interleave and clobber a change."""
    try:
        value = int(float(payload))
    except (TypeError, ValueError):
        LOG.warning("Ignoring non-numeric %s value: %r", which, payload)
        return
    locale = cfg("R5_LOCALE", "en_GB")
    async with _soc_lock_get():
        try:
            async with aiohttp.ClientSession() as websession:
                vehicle = await _login_vehicle(websession, locale)
                soc = await vehicle.get_battery_soc()
                cur_min = getattr(soc, "socMin", None)
                cur_target = getattr(soc, "socTarget", None)
                new_min, new_target = ((value, cur_target) if _NUMBER_ROLE[which] == "min"
                                       else (cur_min, value))
                if new_min is None or new_target is None:
                    LOG.error("Cannot set %s: current limits unavailable (min=%s, target=%s)",
                              which, cur_min, cur_target)
                    return
                await vehicle.set_battery_soc(min=int(new_min), target=int(new_target))
            LOG.info("Set charge limits: min=%s%%, target=%s%%", new_min, new_target)
        except Exception as err:  # noqa: BLE001
            LOG.error("Failed to set %s=%s: %s", which, value, err)


async def run_command(cmd, payload=""):
    """Dispatch an MQTT command: a button press to its renault-api action, or a number set
    to set_battery_soc. Never fatal."""
    if cmd in NUMBER_CMDS:
        await set_soc_level(cmd, payload)
        return
    action = COMMAND_ACTIONS.get(cmd)
    if action is None:
        LOG.warning("Ignoring unknown command: %s", cmd)
        return
    if now_ts() - _last_command.get(cmd, 0) < COMMAND_DEBOUNCE_S:
        LOG.info("Ignoring repeated '%s' within %ds (debounce)", cmd, COMMAND_DEBOUNCE_S)
        return
    _last_command[cmd] = now_ts()
    locale = cfg("R5_LOCALE", "en_GB")
    try:
        async with aiohttp.ClientSession() as websession:
            vehicle = await _login_vehicle(websession, locale)
            await action(vehicle)
        LOG.info("Command '%s' sent", cmd)
    except Exception as err:  # noqa: BLE001
        LOG.error("Command '%s' failed: %s", cmd, err)


# --- Debug API dump (set debug_dump: true on the Configuration page) ----------------
# Vehicle-telemetry endpoints only. Deliberately excludes get_location (GPS),
# get_contracts and get_notification_settings — those carry location / contact / account
# PII with no sensor-mapping diagnostic value. No-arg readers below; date-ranged (charges,
# charge-history) and raw (alerts) endpoints are probed separately in dump_api.
_DEBUG_METHODS = [
    "get_details", "get_car_adapter", "get_battery_status", "get_battery_soc", "get_cockpit",
    "get_hvac_status", "get_hvac_settings", "get_hvac_history", "get_hvac_sessions",
    "get_charge_schedule", "get_charge_mode", "get_charging_settings", "get_tyre_pressure",
    "get_lock_status", "get_res_state",
]
_DEBUG_RANGE_DAYS = 30
# Keys masked regardless of value type — identifiers / contact / location fields.
_DEBUG_REDACT_KEYS = {
    "registrationnumber", "vin", "tcucode", "radiocode", "siret", "msisdn", "phonenumber",
    "phone", "mobile", "email", "firstname", "lastname", "gigyaid", "personid", "accountid",
    "iccid", "imei", "contractid", "address", "postcode", "zipcode", "city", "country",
    "gpslatitude", "gpslongitude", "latitude", "longitude",
}
_DEBUG_STATE = {"dumped": False}


def debug_enabled():
    return cfg("R5_DEBUG_DUMP", "false").strip().lower() in ("true", "1", "on")


def _debug_redact(obj, secrets):
    """Mask identifiers (by key, any value type) + configured secret values; keep telemetry."""
    if isinstance(obj, dict):
        return {k: ("***" if k.lower() in _DEBUG_REDACT_KEYS else _debug_redact(v, secrets))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_debug_redact(v, secrets) for v in obj]
    if isinstance(obj, str):
        for s in secrets:
            if s and s in obj:
                obj = obj.replace(s, "***")
        return obj
    if any(s and s == str(obj) for s in secrets):   # secret value held as a number (e.g. id)
        return "***"
    return obj


async def _dump_one(out, name, call, secrets):
    """Run one debug probe, mask its raw payload, store the result; never fatal."""
    try:
        res = await call()
        if isinstance(res, dict):
            raw = res
        elif isinstance(res, list):
            raw = [getattr(x, "raw_data", x) for x in res]
        else:
            raw = getattr(res, "raw_data", None) or {"_repr": str(res)}
        out[name] = _debug_redact(raw, secrets)
    except Exception as err:  # noqa: BLE001
        out[name] = {"_error": f"{type(err).__name__}: {err}"}


async def dump_api(vehicle):
    """DEBUG: fetch the telemetry endpoints, mask IDs/secrets, log the lot. Never fatal."""
    secrets = [v for v in (cfg("R5_VIN"), cfg("R5_ACCOUNT_ID"), cfg("R5_USERNAME"),
                           cfg("R5_PASSWORD")) if v]
    out = {}
    for meth in _DEBUG_METHODS:
        fn = getattr(vehicle, meth, None)
        if fn is not None:
            await _dump_one(out, meth, lambda _f=fn: _f(), secrets)
    # Date-ranged + raw endpoints can't be called arg-less; probe them explicitly. alerts has
    # no convenience method, so resolve its per-model path and raw-GET it (forbidden -> _error).
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_DEBUG_RANGE_DAYS)

    async def _alerts():
        return await vehicle.http_get(await vehicle.get_full_endpoint("alerts"))

    specials = (
        ("get_charges", getattr(vehicle, "get_charges", None),
         lambda: vehicle.get_charges(start, end)),
        ("get_charge_history", getattr(vehicle, "get_charge_history", None),
         lambda: vehicle.get_charge_history(start, end, "month")),
        ("alerts", getattr(vehicle, "http_get", None), _alerts),
    )
    for name, present, call in specials:
        if present is not None:
            await _dump_one(out, name, call, secrets)
    LOG.warning("API DEBUG DUMP — may contain personal data; redaction is best-effort, do NOT "
                "paste publicly. One-shot per restart; turn debug_dump off when done.\n%s",
                json.dumps(out, indent=2, default=str, ensure_ascii=False))


async def maybe_dump_api(vehicle):
    """Run the debug dump once per restart when debug_dump is on (not every poll)."""
    if debug_enabled() and not _DEBUG_STATE["dumped"]:
        _DEBUG_STATE["dumped"] = True
        await dump_api(vehicle)


def detect_plug_suspect(state, plug, mileage, soc, charging):
    """Connected-but-driven / Disconnected-but-charging detection. `plug` is a PlugState
    (matches the rest of the poll). Returns 'on'/'off'. State stores a JSON-safe string."""
    plugged = plug == PlugState.PLUGGED
    if plugged and (state.get("plug_prev") != "plugged" or "plug_base_ts" not in state):
        state.update(plug_base_mileage=mileage, plug_base_soc=soc, plug_base_ts=now_ts())
    state["plug_prev"] = "plugged" if plugged else "unplugged"

    drove = False
    bts, bkm, bsoc = state.get("plug_base_ts"), state.get("plug_base_mileage"), state.get("plug_base_soc")
    if bts and None not in (mileage, soc, bkm, bsoc):
        age = now_ts() - bts
        if PLUG_MIN_AGE <= age <= PLUG_MAX_AGE:
            drove = (mileage - bkm >= PLUG_KM_DELTA) and (bsoc - soc >= PLUG_SOC_DROP)
    stuck = (plugged and drove) or (plug == PlugState.UNPLUGGED and charging)
    return "on" if stuck else "off"


def update_charge_session(state, battery, capacity_kwh, charging):
    soc = battery.batteryLevel
    power = _num(getattr(battery, "chargingInstantaneousPower", None)) or 0
    energy = _num(getattr(battery, "batteryAvailableEnergy", None))
    if energy is None and soc is not None:
        energy = round(soc / 100.0 * capacity_kwh, 2)

    if charging and not state.get("session_active"):
        LOG.info("Charge session START (soc=%s%%, power=%skW)", soc, power)
        state.update(session_active=True, start_ts=now_ts(), start_soc=soc,
                     start_energy=energy, start_power=power, pwr_accum=0.0, pwr_count=0)
    if charging and state.get("session_active") and power > 0:
        state["pwr_accum"] = state.get("pwr_accum", 0.0) + power
        state["pwr_count"] = state.get("pwr_count", 0) + 1
    if not charging and state.get("session_active"):
        start_ts = state.get("start_ts")
        dur = round((now_ts() - start_ts) / 60.0) if start_ts else None
        avg = round(state["pwr_accum"] / state["pwr_count"], 2) if state.get("pwr_count") else state.get("start_power")
        rec_pct = (soc - state["start_soc"]) if (soc is not None and state.get("start_soc") is not None) else None
        rec_kwh = round(energy - state["start_energy"], 2) if (energy is not None and state.get("start_energy") is not None) else None
        state["last_charge"] = {
            # Keys are the object_id minus the "r5_" prefix, to match the discovery
            # value_template (value_json.<obj-without-prefix>).
            "last_charge_start": iso(start_ts),
            "last_charge_end": iso(now_ts()),
            "last_charge_start_soc": state.get("start_soc"),
            "last_charge_end_soc": soc,
            "last_charge_start_energy": round(state["start_energy"], 2) if state.get("start_energy") is not None else None,
            "last_charge_end_energy": round(energy, 2) if energy is not None else None,
            "last_charge_recovered_pct": rec_pct,
            "last_charge_recovered_kwh": rec_kwh,
            "last_charge_duration_min": dur,
            "last_charge_average_power": avg,
            "last_charge_type": "Rapid/Public" if (avg or 0) > HOME_POWER_MAX_KW else "Home",
        }
        LOG.info("Charge session END (dur=%smin, +%s%%, +%skWh, avg=%skW)", dur, rec_pct, rec_kwh, avg)
        state["session_active"] = False
    return state.get("last_charge", {})


async def resolve_account(client):
    account_id = cfg("R5_ACCOUNT_ID")
    if account_id:
        return account_id
    person = await client.get_person()
    for account in person.accounts:
        if account.accountType == "MYRENAULT":
            LOG.info("Auto-discovered MYRENAULT account")
            LOG.debug("Account id: %s", account.accountId)
            return account.accountId
    raise RuntimeError("No MYRENAULT account found and R5_ACCOUNT_ID not set")


async def poll_once(vsession, state, capacity_kwh, supported_eps, dist_unit):
    vehicle = await vsession.vehicle()
    locale = vsession.locale
    battery = await vehicle.get_battery_status()
    plug = battery.get_plug_status()
    charging = is_charging(battery)
    data = {
        "battery_level": battery.batteryLevel,
        "battery_autonomy": _dist(battery.batteryAutonomy, dist_unit),
        "battery_temperature": battery.batteryTemperature,
        "charging_rate": _num(getattr(battery, "chargingInstantaneousPower", None)),
        "charging_remaining_time": getattr(battery, "chargingRemainingTime", None),
        "available_energy": _num(getattr(battery, "batteryAvailableEnergy", None)),
        "charger_plug_status": _enum_label(plug, PLUG_STATUS_LABELS, getattr(battery, "plugStatus", None)),
        "charging_flap_status": "Open: Plugged In" if plug == PlugState.PLUGGED else "Closed",
        # Match the Charging binary sensor: "Charging" when active, else the ChargeState.
        "charger_status": "Charging" if charging else charging_status_label(battery),
        "battery_last_activity": getattr(battery, "timestamp", None) or iso(now_ts()),
        "drive_side": "RHD" if locale.lower() in RHD_LOCALES else "LHD",
    }
    mileage = None   # keep raw km for plug-suspect distance maths
    try:
        mileage = getattr(await vehicle.get_cockpit(), "totalMileage", None)
        data["vehicle_mileage"] = _dist(mileage, dist_unit)
    except Exception as err:  # noqa: BLE001
        LOG.warning("cockpit unavailable: %s", err)
    try:
        hvac = await vehicle.get_hvac_status()
        data["external_temperature"] = getattr(hvac, "externalTemperature", None)
        # internalTemperature is populated but often only after HVAC activity (else null).
        data["cabin_temperature"] = getattr(hvac, "internalTemperature", None)
        data["hvac_status"] = str(getattr(hvac, "hvacStatus", ""))
        data["hvac_soc_threshold"] = getattr(hvac, "socThreshold", None)
        data["hvac_last_activity"] = getattr(hvac, "lastUpdateTime", None)
    except Exception as err:  # noqa: BLE001
        LOG.warning("hvac unavailable: %s", err)
    try:
        sched = await vehicle.get_charge_schedule()   # KCM ev/settings (preconditioning)
        p = _find_precond(sched)
        data["preconditioning_temperature"] = p.get("preconditioningTemperature")
        data["heated_steering_wheel"] = _bool_on(p.get("preconditioningHeatedStrgWheel"))
        left = p.get("preconditioningHeatedLeftSeat")
        right = p.get("preconditioningHeatedRightSeat")
        rhd = locale.lower() in RHD_LOCALES
        data["heated_seat_driver"] = _bool_on(right if rhd else left)
        data["heated_seat_passenger"] = _bool_on(left if rhd else right)
    except Exception as err:  # noqa: BLE001
        LOG.warning("ev/settings unavailable: %s", err)
    try:
        soc_lvl = await vehicle.get_battery_soc()
        data["soc_max_target"] = getattr(soc_lvl, "socTarget", None)
        data["soc_min_target"] = getattr(soc_lvl, "socMin", None)
    except Exception as err:  # noqa: BLE001
        LOG.warning("battery_soc unavailable: %s", err)
    if "pressure" in supported_eps:
        try:
            tp = await vehicle.get_tyre_pressure()
            data["tyre_pressure_fl"] = getattr(tp, "flPressure", None)
            data["tyre_pressure_fr"] = getattr(tp, "frPressure", None)
            data["tyre_pressure_rl"] = getattr(tp, "rlPressure", None)
            data["tyre_pressure_rr"] = getattr(tp, "rrPressure", None)
        except Exception as err:  # noqa: BLE001
            LOG.warning("tyre_pressure unavailable: %s", err)
    if "charge-mode" in supported_eps:
        try:
            cm = await vehicle.get_charge_mode()
            data["charge_mode"] = str(getattr(cm, "chargeMode", "") or "")
        except Exception as err:  # noqa: BLE001
            LOG.warning("charge_mode unavailable: %s", err)

    location_attrs = None
    try:
        loc = await vehicle.get_location()
        data["gps_last_activity"] = getattr(loc, "lastUpdateTime", None)
        lat, lon = getattr(loc, "gpsLatitude", None), getattr(loc, "gpsLongitude", None)
        if lat is not None and lon is not None:
            location_attrs = {"latitude": lat, "longitude": lon, "gps_accuracy": 10,
                              "last_update": getattr(loc, "lastUpdateTime", None)}
    except Exception as err:  # noqa: BLE001
        LOG.warning("location unavailable: %s", err)

    data.update(update_charge_session(state, battery, capacity_kwh, charging))
    data["charging"] = "on" if charging else "off"
    data["plug_suspect"] = detect_plug_suspect(state, plug, mileage,
                                               battery.batteryLevel, charging)
    await maybe_dump_api(vehicle)
    return data, location_attrs


HEALTH_PORT = 8099

# Latest poll snapshot for the read-only ingress status panel. Deliberately excludes raw
# GPS (lat/lon are published separately to MQTT, not stored here) and any credential.
_LATEST = {"version": VERSION, "ok": False, "last_poll": None, "supported": [],
           "dist_unit": "km", "data": {}}

_PANEL_FILE = os.path.join(os.path.dirname(__file__), "panel.html")


async def _panel_page(_req):
    """Serve the self-contained, read-only ingress status panel."""
    if os.path.isfile(_PANEL_FILE):
        return web.FileResponse(_PANEL_FILE)
    return web.Response(text="Status panel unavailable", content_type="text/plain")


async def _panel_state(_req):
    """JSON the panel polls — latest car state + diagnostics; no credentials, no raw GPS."""
    return web.json_response(_LATEST)


async def start_health_server():
    """/healthz (backs the Dockerfile HEALTHCHECK) plus the read-only ingress status panel
    (GET / and GET /api/state) on the poll loop. A deadlocked loop can't answer /healthz,
    so the Supervisor marks the container unhealthy and restarts it."""
    app = web.Application()
    app.router.add_get("/healthz", lambda _req: web.Response(text="ok"))
    app.router.add_get("/", _panel_page)
    app.router.add_get("/api/state", _panel_state)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HEALTH_PORT).start()  # nosec B104
    LOG.info("Health endpoint + status panel listening on :%d", HEALTH_PORT)
    return runner


async def main():
    global _LOOP
    setup_logging()
    LOG.info("Renault 5 add-on v%s starting", VERSION)
    for req in ("R5_USERNAME", "R5_PASSWORD", "R5_VIN", "MQTT_HOST"):
        if not cfg(req):
            LOG.error("Missing required setting: %s — set it on the add-on Configuration page.", req)
            sys.exit(1)

    locale = cfg("R5_LOCALE", "en_GB")
    dist_unit = "mi" if locale.lower() in MILES_LOCALES else "km"
    interval = int(cfg("R5_POLL_INTERVAL", "300") or "300")
    capacity = float(cfg("R5_BATTERY_CAPACITY_KWH", "52") or "52")
    stale_secs = int(cfg("R5_STALE_HOURS", "6") or "6") * 3600

    _LOOP = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):   # honour shutdown during startup too
        _LOOP.add_signal_handler(sig, stop.set)

    health = await start_health_server()
    state = load_state()
    vsession = VehicleSession(locale)
    supported = await detect_supported(vsession)
    _LATEST["supported"] = sorted(supported)
    _LATEST["dist_unit"] = dist_unit  # so the status panel can label range/mileage
    client = mqtt_connect()
    publish_discovery(client, supported, dist_unit)
    await deploy.run_deploy()  # optional dashboard auto-deploy; never fatal

    fails = 0
    while not stop.is_set():
        try:
            t0 = time.monotonic()
            data, location_attrs = await asyncio.wait_for(   # a hung poll can't stall the loop
                poll_once(vsession, state, capacity, supported, dist_unit),
                timeout=max(30, interval - 10))
            state["last_success"] = now_ts()
            data["api_auth_failure"] = "off"
            data["data_stale"] = "off"
            client.publish(STATE_TOPIC, json.dumps(data), retain=True)
            _LATEST.update(ok=True, last_poll=iso(now_ts()), data=data)
            if location_attrs:
                client.publish(ATTR_TOPIC, json.dumps(location_attrs), retain=True)
                client.publish(TRACKER_STATE_TOPIC, "online", retain=True)
            client.publish(AVAIL_TOPIC, "online", retain=True)
            save_state(state)
            fails = 0
            LOG.info("Published in %.1fs: %s%% battery, plug=%s, charging=%s, suspect=%s",
                     time.monotonic() - t0, data.get("battery_level"),
                     data.get("charger_plug_status"), data.get("charging"),
                     data.get("plug_suspect"))
        except Exception as err:  # noqa: BLE001
            fails += 1
            LOG.error("Poll failed (%d in a row): %s", fails, err)
            await vsession.invalidate()   # next cycle re-authenticates (self-heal)
            last_ok = state.get("last_success", 0)
            stale = (now_ts() - last_ok) > stale_secs if last_ok else True
            auth = any(s in str(err).lower() for s in ("login", "password", "credential", "401", "403"))
            client.publish(STATE_TOPIC, json.dumps({
                "api_auth_failure": "on" if auth else "off",
                "data_stale": "on" if stale else "off",
            }), retain=True)
            client.publish(AVAIL_TOPIC, "online", retain=True)
            _LATEST.update(ok=False, last_poll=iso(now_ts()), error=str(err))
            _LATEST["data"].update(api_auth_failure="on" if auth else "off",
                                   data_stale="on" if stale else "off")
        # exponential backoff on repeated failures (avoid a re-auth storm), capped at 30 min
        delay = interval if fails == 0 else min(interval * 2 ** (fails - 1), 1800)
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    LOG.info("Shutting down")
    await vsession.close()
    await health.cleanup()
    client.publish(AVAIL_TOPIC, "offline", retain=True)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
