"""Debug API-dump seam for the Renault 5 add-on.

The one-shot `debug_dump` diagnostic: fetch the readable endpoints once per restart, redact
identifiers/secrets from the raw payloads, and log the lot at WARNING. Split out of main so the
poll loop stays lean and the redaction logic sits next to the dump it protects.

Imports only `config` (the leaf seam) for `cfg` + `_config_secrets`, so it has no cycle with
main. `_debug_redact` here is the *structural* key/value scrubber for decoded API payloads —
distinct from config.redact, the substring scrubber for arbitrary log strings; the dump uses
both (config's secret list feeds this module's value masking).
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from config import _config_secrets, cfg

LOG = logging.getLogger("renault_5")

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
    # Vehicle-lifecycle / privacy / build-spec — quasi-identifying or owner-private. The
    # `assets` block carries 3dv.renault.com render URLs that embed the build-spec (VCD)
    # code in the path; mask the whole subtree (it has no sensor-mapping diagnostic value).
    "deliverydate", "firstregistrationdate", "vehicleid", "batterycode",
    "privacymode", "privacymodeupdatedate", "svtflag", "svtblockflag", "assets",
    # Defense-in-depth: token/credential field names. The endpoint allowlist + logger floor
    # already keep tokens out of the dump; this guards a future token-bearing payload too.
    "token", "accesstoken", "refreshtoken", "idtoken", "jwt", "authorization", "apikey",
    "secret", "password", "gigyacookievalue",
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
    secrets = _config_secrets()
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
