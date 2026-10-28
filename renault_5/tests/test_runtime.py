"""Runtime-path tests for the Renault 5 poller and dashboard deployer.

Complements test_main.py (pure helpers) by exercising the side-effecting paths that stay in
main: the cached-login failure modes, endpoint-support detection, command dispatch, account
resolution, the main() poll loop (success and failure), the extra poll_once endpoint branches,
and the whole deploy.py WebSocket flow. The config/util/debug/charge/mqtt seams are covered in
their own test modules (only the retargeted monkeypatch targets appear here). Tests stay
synchronous and drive coroutines with asyncio.run (so the suite needs only pytest, not
pytest-asyncio).
"""
import ast
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import deploy
import main
import pytest
from renault_api.exceptions import NotAuthenticatedException
from renault_api.gigya.exceptions import PendingTwoFactorAuthenticationException
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_api.kamereon.exceptions import InvalidInputException, UnauthorizedException
from renault_mqtt import config, debug, mqtt


def _obj(**attrs):
    return type("Obj", (), attrs)()


# --------------------------------------------------------------------------- #
# tiny helpers
# --------------------------------------------------------------------------- #
def test_setup_logging_runs(monkeypatch):
    monkeypatch.setenv("R5_LOG_LEVEL", "debug")
    main.setup_logging()        # exercises the level lookup; basicConfig is a no-op under pytest
    monkeypatch.setenv("R5_LOG_LEVEL", "nonsense")
    main.setup_logging()        # invalid level falls back to INFO


_LIBRARY_LOGGERS = ("renault_api", "renault_api.gigya", "renault_api.kamereon", "renault_api.kamereon.models",
                    "renault_api.renault_session", "renault_api.renault_account", "renault_api.renault_client")


def test_setup_logging_keeps_library_debug_off_at_debug(monkeypatch):
    # pytest's root handlers make basicConfig a no-op and leave root at WARNING, which would
    # pass this with no clamp at all. Strip them so basicConfig really sets root to DEBUG.
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_levels = {name: logging.getLogger(name).level for name in _LIBRARY_LOGGERS}
    root.handlers.clear()
    try:
        monkeypatch.setenv("R5_LOG_LEVEL", "debug")
        main.setup_logging()
        assert root.level == logging.DEBUG                       # the precondition is real
        assert main.LOG.isEnabledFor(logging.DEBUG)              # our own debug still flows
        for name in _LIBRARY_LOGGERS:                            # the library's does not
            assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)


# --------------------------------------------------------------------------- #
# state persistence
# --------------------------------------------------------------------------- #
def test_state_roundtrip(monkeypatch, tmp_path):
    f = tmp_path / "state.json"
    monkeypatch.setattr(main, "STATE_FILE", str(f))
    assert main.load_state() == {}            # missing file
    main.save_state({"a": 1})
    assert main.load_state() == {"a": 1}
    f.write_text("{not json")
    assert main.load_state() == {}            # corrupt file


def test_save_state_swallows_oserror(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(main.os, "replace", boom)
    main.save_state({"a": 1})                 # must not raise


# --------------------------------------------------------------------------- #
# MQTT callbacks + connect
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self, *a, **k):
        self.subs, self.pubs, self.attrs = [], [], {}
        self.snapshots = self
        self.connected = True
        self.latest = None
        self.location = None
        self.closed = False

    def update(self, data, **kwargs):
        self.latest = json.dumps(data)
        if not mqtt.PUBLISH_LOCATION:
            self.location = ""
        elif "location" in kwargs:
            self.location = json.dumps(kwargs["location"]) if kwargs["location"] else ""
        self.replay()

    def replay(self):
        if self.connected and self.latest is not None and not self.closed:
            self.publish(mqtt.STATE_TOPIC, self.latest, retain=True)
            if self.location is not None:
                self.publish(mqtt.ATTR_TOPIC, self.location, retain=True)
            self.publish(mqtt.AVAIL_TOPIC, "online", retain=True)

    def connect(self):
        self.connected = True
        mqtt.publish_discovery(self, mqtt._MQTT_CTX["supported"], mqtt._MQTT_CTX["dist_unit"])
        self.replay()

    async def close(self):
        self.closed = True
        self.publish(mqtt.AVAIL_TOPIC, "offline", retain=True)
        self.disconnect()
        self.loop_stop()

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def username_pw_set(self, u, p):
        self.creds = (u, p)

    def will_set(self, *a, **k):
        self.will = (a, k)

    def reconnect_delay_set(self, **k):
        self.delay = k

    def subscribe(self, topic):
        self.subs.append(topic)

    def publish(self, topic, payload="", retain=False):
        self.pubs.append((topic, payload, retain))

    def connect_async(self, host, port, keepalive=60):
        self.conn = (host, port, keepalive)

    def loop_start(self):
        self.started = True

    def loop_stop(self):
        self.stopped = True

    def disconnect(self):
        self.disconnected = True


# (MQTT callbacks + connect are covered in test_mqtt.py; _FakeClient stays here because the
# main() loop tests below wire it in via _wire_main.)


# --------------------------------------------------------------------------- #
# cached login session — failure modes
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# endpoint-support detection
# --------------------------------------------------------------------------- #
def test_supports_awaits_the_pinned_api():
    """supports_endpoint() is a coroutine in the pinned renault-api, so its result is awaited."""
    class V:
        async def supports_endpoint(self, ep):
            return ep == "supported"

    async def scenario():
        assert await main._supports(V(), "supported") is True
        assert await main._supports(V(), "other") is False

    asyncio.run(scenario())


def test_detect_supported_probes_endpoints():
    actions = {ep for *_rest, ep in main.ACTION_BUTTONS.values()}

    class V:
        async def supports_endpoint(self, ep):
            return ep != "pressure"      # pressure unsupported; charge-mode + all actions ok

    class Sess:
        async def vehicle(self):
            return V()

        async def invalidate(self):
            pass

    async def scenario():
        sup = await main.detect_supported(Sess())
        assert "charge-mode" in sup and "pressure" not in sup
        assert "hvac-settings" in sup            # pessimistic, but a probe that says yes adds it
        assert main._hvac_due(sup) is True
        assert actions <= sup
        assert main.SOC_ENDPOINT in sup          # soc-levels probed and supported

    asyncio.run(scenario())


def test_detect_supported_hides_actions_on_failure():
    class Sess:
        async def vehicle(self):
            raise RuntimeError("api down")

        async def invalidate(self):
            self.invalidated = True

    async def scenario():
        sess = Sess()
        sup = await main.detect_supported(sess)
        # Plain data endpoints default on; the pessimistic ones stay dark rather than turn one
        # failed detection into a 502000 on every poll. Buttons are hidden either way.
        assert sup == set(main.OPTIONAL_ENDPOINTS) - main.PESSIMISTIC_ENDPOINTS
        assert "hvac-settings" not in sup
        assert main._hvac_due(sup) is False
        assert sess.invalidated is True

    asyncio.run(scenario())


def test_detection_wires_catalog_defaults_probe_logging_and_pending_retry(caplog):
    calls, failed = [], []

    class V:
        async def supports_endpoint(self, ep):
            calls.append(ep)
            if len(failed) < len(main.SUPPORT_ENDPOINTS):
                failed.append(ep)
                raise RuntimeError("probe unavailable")
            return ep == main.SOC_ENDPOINT

    class Session:
        invalidated = False

        async def vehicle(self):
            return V()

        async def invalidate(self):
            self.invalidated = True

    vs = Session()
    pending = set(main.SUPPORT_ENDPOINTS)
    with caplog.at_level(logging.INFO):
        supported = asyncio.run(main.detect_supported(vs, unresolved=pending))
        assert supported == set(main.OPTIONAL_ENDPOINTS) - main.PESSIMISTIC_ENDPOINTS
        assert pending == main.SUPPORT_ENDPOINTS
        assert calls == sorted(main.SUPPORT_ENDPOINTS)
        assert not vs.invalidated
        assert asyncio.run(main.detect_supported(vs, supported, pending)) is supported
    assert supported == {main.SOC_ENDPOINT} and pending == set()
    assert calls == sorted(main.SUPPORT_ENDPOINTS) * 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [f"supports_endpoint({ep}) check failed: probe unavailable" for ep in sorted(main.SUPPORT_ENDPOINTS)]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO] == [
        f"Supported optional endpoints: {sorted(set(main.OPTIONAL_ENDPOINTS) - main.PESSIMISTIC_ENDPOINTS)}",
        f"Supported optional endpoints: {[main.SOC_ENDPOINT]}",
    ]


@pytest.mark.parametrize("cancelled", [False, True])
def test_detection_outer_failure_adapters_preserve_sets_and_propagate_cancellation(monkeypatch, caplog, cancelled):
    error = asyncio.CancelledError() if cancelled else RuntimeError("synthetic outer failure")
    invalidated, redacted = [], []

    class VS:
        async def vehicle(self):
            raise error

        async def invalidate(self):
            invalidated.append(True)

    def redact(err):
        redacted.append(err)
        return "masked failure"

    monkeypatch.setattr(main, "redact", redact)
    supported, pending = {"known"}, set(main.SUPPORT_ENDPOINTS)
    if cancelled:
        with pytest.raises(asyncio.CancelledError) as caught:
            asyncio.run(main.detect_supported(VS(), supported, pending))
        assert caught.value is error
        assert invalidated == redacted == [] and caplog.records == []
    else:
        assert asyncio.run(main.detect_supported(VS(), supported, pending)) is supported
        assert invalidated == [True] and redacted == [error]
        assert [r.getMessage() for r in caplog.records] == [
            "Endpoint-support detection failed (keeping prior support): masked failure",
        ]
    assert supported == {"known"} and pending == main.SUPPORT_ENDPOINTS


# --------------------------------------------------------------------------- #
# command dispatch
# --------------------------------------------------------------------------- #
def test_hvac_start_falls_back_to_21_on_schedule_error(monkeypatch):
    monkeypatch.delenv("R5_PRECONDITION_TEMPERATURE", raising=False)

    class V:
        def __init__(self):
            self.temp = None

        async def get_charge_schedule(self):
            raise RuntimeError("no schedule")

        async def set_ac_start(self, t):
            self.temp = t

    v = V()
    asyncio.run(main._hvac_start_action(v))
    assert v.temp == 21.0


class _CmdSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _fake_client_session(monkeypatch):
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: _CmdSession())


def test_the_debounce_clock_is_monotonic(monkeypatch):
    monkeypatch.setattr(main.time, "monotonic", lambda: 42.0)
    monkeypatch.setattr(main.time, "time", lambda: 1_000_000.0)
    assert main._debounce_now() == 42.0


class _Horn:
    def __init__(self, fail=False):
        self.fail, self.honks = fail, 0

    async def start_horn(self):
        self.honks += 1
        if self.fail:
            raise RuntimeError("action failed at the car")


def _bounded(coro):
    """Run a scenario that parks logins on an event, failing instead of hanging if a regression
    leaves a command waiting on a login nobody releases."""
    return asyncio.run(asyncio.wait_for(coro, 2))


def test_climate_stop_waits_for_the_earlier_start_login(monkeypatch):
    async def scenario():
        entered, release, submitted = asyncio.Event(), asyncio.Event(), asyncio.Event()
        logins, actions = [], []

        class V(_ChargingVehicle):
            async def set_ac_start(self, temp):
                actions.append("start")

            async def set_ac_stop(self):
                actions.append("stop")

        async def login(ws, loc):
            name = asyncio.current_task().get_name()
            logins.append(name)
            if name == "start":
                entered.set()
                await release.wait()
            return V()

        async def stop():
            submitted.set()
            await main.run_command("hvac_stop")

        _fake_client_session(monkeypatch)
        monkeypatch.setattr(main, "_login_vehicle", login)
        tasks = [asyncio.create_task(main.run_command("hvac_start"), name="start")]
        try:
            await entered.wait()
            tasks.append(asyncio.create_task(stop(), name="stop"))
            await submitted.wait()
            blocked_logins, blocked_actions = list(logins), list(actions)
            release.set()
            await asyncio.gather(*tasks)
            assert (blocked_logins, blocked_actions, actions) == (["start"], [], ["start", "stop"]), \
                "Stop overtook the blocked Start login"
            assert logins == ["start", "stop"]
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    _bounded(scenario())


@pytest.mark.parametrize("publish_location,enable_refresh,dispatched", [
    (True,  True,  True),    # opted in -> the only combination that reaches the car
    (True,  False, False),   # the shipped default
    (False, True,  False),
    (False, False, False),
])
def test_run_command_gates_refresh_location_on_the_opt_in(
        monkeypatch, publish_location, enable_refresh, dispatched):
    """Withholding the discovery button is not enough on its own: the entity is pressable from
    voice, automations and any dashboard, all of which publish to the same command topic. So the
    command itself must be rejected, and rejected BEFORE the login - a refused press must not even
    authenticate, let alone reach actions/refresh-location."""
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: _CmdSession())
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", publish_location)
    monkeypatch.setattr(mqtt, "ENABLE_REFRESH_LOCATION", enable_refresh)
    logins = {"n": 0}
    refreshed = {"n": 0}

    class Veh:
        async def refresh_location(self):
            refreshed["n"] += 1

    async def fake_login(ws, locale):
        logins["n"] += 1
        return Veh()

    monkeypatch.setattr(main, "_login_vehicle", fake_login)
    (cmd,) = tuple(main.LOCATION_CMDS)
    asyncio.run(main.run_command(cmd))

    assert refreshed["n"] == (1 if dispatched else 0)
    assert logins["n"] == (1 if dispatched else 0)


# --------------------------------------------------------------------------- #
# charge-limit numbers (set_battery_soc)
# --------------------------------------------------------------------------- #
class _SocVehicle:
    def __init__(self):
        self.soc_set = None

    async def get_battery_soc(self):
        return _obj(socTarget=80, socMin=20)

    async def set_battery_soc(self, *, min, target):
        self.soc_set = (min, target)


def _soc_login(monkeypatch, vehicle):
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: _CmdSession())

    async def login(ws, loc):
        return vehicle

    monkeypatch.setattr(main, "_login_vehicle", login)


def test_set_soc_max_target_sends_both_limits(monkeypatch):
    v = _SocVehicle()
    _soc_login(monkeypatch, v)
    asyncio.run(main.run_command("soc_max_target", "90"))
    assert v.soc_set == (20, 90)         # min unchanged, target updated


def test_set_soc_min_target_sends_both_limits(monkeypatch):
    v = _SocVehicle()
    _soc_login(monkeypatch, v)
    asyncio.run(main.run_command("soc_min_target", "30"))
    assert v.soc_set == (30, 80)         # target unchanged, min updated


def test_set_soc_enforces_catalog_number_ranges(monkeypatch):
    v = _SocVehicle()
    _soc_login(monkeypatch, v)
    min_lo, min_hi, min_step = main.NUMBER_RANGES["soc_min_target"]
    tgt_lo, tgt_hi, tgt_step = main.NUMBER_RANGES["soc_max_target"]
    assert (min_lo, min_hi, min_step) == main.NUMBERS["r5_soc_min_target"][2:5]
    assert (tgt_lo, tgt_hi, tgt_step) == main.NUMBERS["r5_soc_max_target"][2:5]
    asyncio.run(main.run_command("soc_max_target", str(tgt_hi + 1)))
    asyncio.run(main.run_command("soc_min_target", str(min_lo + 1)))
    assert v.soc_set is None
    asyncio.run(main.run_command("soc_min_target", str(min_lo)))
    assert v.soc_set == (min_lo, 80)


def test_run_command_debounces_and_clears_stamp_when_login_fails(monkeypatch):
    v, logins = _Horn(), {"n": 0}
    gate = None

    async def login(ws, loc):
        logins["n"] += 1
        if gate is not None:
            await gate.wait()
        if logins["n"] == 1:
            raise RuntimeError("login failed")
        return v

    _fake_client_session(monkeypatch)
    monkeypatch.setattr(main, "_login_vehicle", login)
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "_debounce_now", lambda: clock["t"])

    async def in_flight_check():
        nonlocal gate
        gate = asyncio.Event()
        first = asyncio.create_task(main.run_command("horn"))
        await asyncio.sleep(0)
        assert "horn" in main._in_flight
        clock["t"] = 1006.0
        await main.run_command("horn")
        gate.set()
        await first
        assert "horn" not in main._in_flight

    _bounded(in_flight_check())
    gate = None
    clock["t"] = 1007.0
    asyncio.run(main.run_command("horn"))
    clock["t"] = 1008.0
    asyncio.run(main.run_command("horn"))
    assert (logins["n"], v.honks) == (2, 1)


def test_poll_once_debug_dump_is_redacted_and_one_shot(monkeypatch, caplog):
    monkeypatch.setitem(debug._DEBUG_STATE, "dumped", False)
    monkeypatch.setenv("R5_DEBUG_DUMP", "true")
    monkeypatch.setenv("R5_VIN", "VF1DEBUGVIN0001")
    probes = []

    class V(_ChargingVehicle):
        async def get_details(self):
            probes.append("details")
            return _obj(raw_data={"vin": "PRIVATEVIN", "batteryLevel": 60,
                                  "diagnostic": "vehicle VF1DEBUGVIN0001"})

    with caplog.at_level(logging.WARNING, logger=debug.LOG.name):
        for _ in range(2):
            asyncio.run(main.poll_once(_Sess(V()), {}, 52.0, set(), "km"))
    dumps = [r for r in caplog.records if r.name == debug.LOG.name and "API DEBUG DUMP" in r.getMessage()]
    assert probes == ["details"]
    assert len(dumps) == 1
    payload = json.loads(dumps[0].getMessage().split("\n", 1)[1])
    assert payload["get_details"] == {"vin": "***", "batteryLevel": 60, "diagnostic": "vehicle ***"}
    assert "PRIVATEVIN" not in caplog.text and "VF1DEBUGVIN0001" not in caplog.text


def test_login_vehicle(monkeypatch):
    for key, value in {"R5_USERNAME": "u", "R5_PASSWORD": "p", "R5_VIN": "V",
                       "R5_ACCOUNT_ID": "acct"}.items():
        monkeypatch.setenv(key, value)
    websession = object()

    class Session:
        async def login(self, u, p):
            assert (u, p) == ("u", "p")

    class Account:
        async def get_api_vehicle(self, vin):
            assert vin == "V"
            return "VEHICLE"

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"websession": websession, "locale": "en_GB"}
            self.session = Session()

        async def get_api_account(self, aid):
            assert aid == "acct"
            return Account()

    monkeypatch.setattr(main, "RenaultClient", Client)
    assert asyncio.run(main._login_vehicle(websession, "en_GB")) == "VEHICLE"


# --------------------------------------------------------------------------- #
# account resolution
# --------------------------------------------------------------------------- #
def test_resolve_account_uses_configured_id(monkeypatch):
    monkeypatch.setenv("R5_ACCOUNT_ID", "ACC-CONFIGURED")
    assert asyncio.run(main.resolve_account(object())) == "ACC-CONFIGURED"


def test_resolve_account_autodiscovers_myrenault(monkeypatch):
    # No VIN configured -> fall back to the MYRENAULT account (legacy safety net).
    monkeypatch.delenv("R5_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R5_VIN", raising=False)

    class Client:
        async def get_person(self):
            return _obj(accounts=[_obj(accountType="OTHER", accountId="x"),
                                  _obj(accountType="MYRENAULT", accountId="ACC-9")])

    assert asyncio.run(main.resolve_account(Client())) == "ACC-9"
    # the discovered id is captured (in the config seam) so redact() can mask it in later URLs
    assert config._DISCOVERED_ACCOUNT_ID == "ACC-9"
    assert "ACC-9" not in config.redact("boom /accounts/ACC-9/vehicles/V/x")


def test_resolve_account_matches_vin_garage(monkeypatch):
    # Pick the account whose garage holds the VIN, not the one whose type is MYRENAULT
    # (keeps parity with the A290, whose car is under MYALPINE).
    monkeypatch.delenv("R5_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("R5_VIN", "vf1r5vin0000000001")  # lower-case: match must be case-insensitive
    garages = {"acct-a": [], "acct-b": [_obj(vin="VF1R5VIN0000000001")]}

    class Account:
        def __init__(self, aid):
            self.aid = aid

        async def get_vehicles(self):
            return _obj(vehicleLinks=garages[self.aid])

    class Client:
        async def get_person(self):
            return _obj(accounts=[_obj(accountType="MYRENAULT", accountId="acct-a"),
                                  _obj(accountType="MYRENAULT", accountId="acct-b")])

        async def get_api_account(self, aid):
            return Account(aid)

    assert asyncio.run(main.resolve_account(Client())) == "acct-b"
    assert config._DISCOVERED_ACCOUNT_ID == "acct-b"


def test_resolve_account_skips_failing_account(monkeypatch):
    # A garage lookup that errors on one account must not abort discovery — keep scanning.
    monkeypatch.delenv("R5_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("R5_VIN", "VF1R5VIN0000000001")

    class Account:
        def __init__(self, aid):
            self.aid = aid

        async def get_vehicles(self):
            if self.aid == "acct-bad":
                raise RuntimeError("kamereon 500")
            return _obj(vehicleLinks=[_obj(vin="VF1R5VIN0000000001")])

    class Client:
        async def get_person(self):
            return _obj(accounts=[_obj(accountType="SFDC", accountId="acct-bad"),
                                  _obj(accountType="MYRENAULT", accountId="acct-good")])

        async def get_api_account(self, aid):
            return Account(aid)

    assert asyncio.run(main.resolve_account(Client())) == "acct-good"


def test_resolve_account_raises_when_no_myrenault(monkeypatch):
    monkeypatch.delenv("R5_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R5_VIN", raising=False)

    class Client:
        async def get_person(self):
            return _obj(accounts=[_obj(accountType="OTHER", accountId="x")])

    with pytest.raises(RuntimeError):
        asyncio.run(main.resolve_account(Client()))


# --------------------------------------------------------------------------- #
# poll_once — charging path + total endpoint failure
# --------------------------------------------------------------------------- #
class _Sess:
    def __init__(self, vehicle, locale="en_GB"):
        self._v = vehicle
        self.locale = locale

    async def vehicle(self):
        return self._v

    async def invalidate(self):
        pass

    async def close(self):
        pass


class _ChargingBattery:
    batteryLevel = 55
    batteryAutonomy = 150
    batteryTemperature = 15
    chargingInstantaneousPower = 7.4
    chargingRemainingTime = 90
    batteryAvailableEnergy = 28.0
    plugStatus = 1
    timestamp = None                      # a payload that carries no report time

    def get_plug_status(self):
        return PlugState.PLUGGED

    def get_charging_status(self):
        return ChargeState.CHARGE_IN_PROGRESS


class _ChargingVehicle:
    async def get_battery_status(self):
        return _ChargingBattery()

    async def get_cockpit(self):
        return _obj(totalMileage=100)

    async def get_hvac_status(self):
        return _obj(externalTemperature=10, internalTemperature=None,
                    hvacStatus="on", socThreshold=40, lastUpdateTime="t")

    async def get_charge_schedule(self):
        return {"preconditioningTemperature": 20, "chargeModeRq": "scheduled_charge",
                "chargeTimeStart": "0230", "chargeDuration": 360}

    async def get_hvac_settings(self):
        day = _obj(readyAtTime="T07:15Z")
        sched = _obj(activated=True, monday=day, tuesday=None, wednesday=None,
                     thursday=None, friday=None, saturday=None, sunday=None)
        return _obj(mode="scheduled", schedules=[sched])

    async def get_battery_soc(self):
        return _obj(socTarget=90, socMin=10)

    async def get_location(self):
        return _obj(gpsLatitude=None, gpsLongitude=None, lastUpdateTime="t")


def test_poll_once_charging_and_no_gps_fix():
    data, loc = asyncio.run(main.poll_once(_Sess(_ChargingVehicle()), {}, 52.0,
                                           {"hvac-settings"}, "km"))
    assert data["charging"] == "on"
    assert data["charger_status"] == "Charging"
    assert data["available_energy"] == 28.0               # reported by the car, used verbatim
    assert data["charging_flap_status"] == "Open: Plugged In"
    # NOT fabricated. The poller used to fall back to iso(now_ts()) here, stamping a payload
    # that carried no timestamp as if it had arrived this instant — which would make staleness
    # permanently unfireable. If the car did not say when it reported, we do not know.
    assert data["battery_last_activity"] is None
    assert data["charge_schedule_mode"] == "Scheduled Charge"   # from the KCM ev/settings payload
    assert data["scheduled_charge_start"] == "02:30" and data["scheduled_charge_duration"] == 360
    assert data["climate_schedule_mode"] == "Scheduled" and data["climate_ready_time"] == "Mon 07:15"
    assert loc is None                                    # lat/lon both None => no tracker attrs


def test_poll_once_rounds_gps_for_privacy():
    class _GpsVehicle(_ChargingVehicle):
        async def get_location(self):
            return _obj(gpsLatitude=51.512345, gpsLongitude=-0.123456, lastUpdateTime="t")  # synthetic-coords: 6 dp load-bearing, rounding asserted below

    _data, loc = asyncio.run(main.poll_once(_Sess(_GpsVehicle()), {}, 52.0, set(), "km"))
    assert loc["latitude"] == 51.5123 and loc["longitude"] == -0.1235   # synthetic-coords: the fixture above, rounded to 4 dp
    assert loc["gps_accuracy"] == 11                                    # ~11 m at 4 dp


# --------------------------------------------------------------------------- #
# main() — one full poll cycle (success + failure branch)
# --------------------------------------------------------------------------- #
def _bound_port(runner):
    # HEALTH_PORT is patched to 0, so the OS picks a free port; read back the one it chose.
    return runner.addresses[0][1]


def test_health_server_serves_200(monkeypatch):
    import aiohttp

    monkeypatch.setattr(main, "HEALTH_PORT", 0)

    async def scenario():
        runner = await main.start_health_server()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{_bound_port(runner)}/healthz") as r:
                    assert r.status == 200 and (await r.text()) == "ok"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_status_panel_routes(monkeypatch):
    import aiohttp

    monkeypatch.setattr(main, "HEALTH_PORT", 0)

    async def scenario():
        main._LATEST.update(ok=True, version="testver", supported=["x", "y"],
                            data={"battery_level": 42, "charger_plug_status": "Plugged"})
        runner = await main.start_health_server()
        base = f"http://127.0.0.1:{_bound_port(runner)}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{base}/api/state") as r:        # JSON snapshot
                    assert r.status == 200
                    body = await r.json()
                    assert body["ok"] is True and body["version"] == "testver"
                    assert body["data"]["battery_level"] == 42
                async with s.get(f"{base}/") as r:                 # panel HTML
                    assert r.status == 200 and "Renault 5" in (await r.text())
                monkeypatch.setattr(main, "_PANEL_FILE", "/no/such/panel.html")
                async with s.get(f"{base}/") as r:                 # graceful fallback
                    assert r.status == 200 and "unavailable" in (await r.text())
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


@pytest.fixture
def wire_main(monkeypatch, tmp_path):
    """Main-loop scenarios use real persistence in their own directory."""
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    return lambda poll: _wire_main(monkeypatch, poll)


def _wire_main(monkeypatch, poll):
    for k, v in {"R5_USERNAME": "u", "R5_PASSWORD": "p", "R5_VIN": "VF1",
                 "MQTT_HOST": "broker", "R5_LOCALE": "en_GB"}.items():
        monkeypatch.setenv(k, v)
    fc = _FakeClient()
    # main() calls these qualified as mqtt.mqtt_connect() / mqtt.publish_discovery().
    def connect(*, snapshots=False):
        assert snapshots, "The app must opt into startup snapshot delivery"
        return fc

    monkeypatch.setattr(mqtt, "mqtt_connect", connect)
    monkeypatch.setattr(mqtt, "publish_discovery", lambda *a, **k: None)

    async def fake_detect(vs, *, unresolved=None):
        if unresolved is not None:
            unresolved.clear()
        return set(main.OPTIONAL_ENDPOINTS)

    monkeypatch.setattr(main, "detect_supported", fake_detect)

    fc.deploy_calls = []

    async def fake_deploy(**kw):
        fc.deploy_calls.append(kw)

    monkeypatch.setattr(main.deploy, "run_deploy", fake_deploy)

    async def fake_health(health_state=None):      # don't bind a real port in the loop test
        class _Runner:
            async def cleanup(self):
                pass
        return _Runner()

    monkeypatch.setattr(main, "start_health_server", fake_health)

    created = {}
    real_event = main.asyncio.Event

    def make_event():
        e = real_event()
        created.setdefault("stop", e)
        return e

    monkeypatch.setattr(main.asyncio, "Event", make_event)
    monkeypatch.setattr(main, "poll_once",
                        lambda *a, **k: poll(created["stop"], *a, **k))
    return fc


def _main_cycles(monkeypatch, count, after_cycle):
    """Advance at main's inter-poll wait, after publications and real persistence."""
    class Stop:
        n = 0

        def is_set(self):
            return self.n >= count

        def set(self):
            self.n = count

        async def wait(self):
            await after_cycle(self.n)
            self.n += 1
            raise asyncio.TimeoutError

    with monkeypatch.context() as scoped:
        scoped.setattr(main.asyncio, "Event", Stop)
        _bounded(main.main())


@pytest.mark.parametrize("failure", ["login", "probe"])
def test_main_recovers_discovery_after_transient_detection_failure(monkeypatch, wire_main, failure):
    real_detect, real_discovery, real_poll = main.detect_supported, mqtt.publish_discovery, main.poll_once
    probes, logins = {}, []
    expected = {"actions/horn-start", "actions/lights-start", main.SOC_ENDPOINT}

    class V(_ChargingVehicle):
        async def supports_endpoint(self, ep):
            probes[ep] = probes.get(ep, 0) + 1
            if failure == "probe":
                if ep in {"actions/horn-start", main.SOC_ENDPOINT} and probes[ep] <= 2:
                    raise RuntimeError("temporary probe outage")
                if ep == "actions/lights-start" and probes[ep] > 1:
                    raise RuntimeError("known-positive probe unavailable")
            return ep in expected

    class VS(_Sess):
        def __init__(self, locale):
            super().__init__(V(), locale)

        async def vehicle(self):
            logins.append("login")
            if failure == "login" and len(logins) == 1:
                raise RuntimeError("temporary login outage")
            return await super().vehicle()

    fc = wire_main(real_poll)
    monkeypatch.setattr(main, "poll_once", real_poll)
    monkeypatch.setattr(main, "VehicleSession", VS)
    monkeypatch.setattr(main, "detect_supported", real_detect)
    monkeypatch.setattr(mqtt, "publish_discovery", real_discovery)
    fc.connected = False
    snapshots = []

    async def after_cycle(n):
        if n == 0:
            fc.connect()
            if failure == "probe":
                configs = {t: p for t, p, _ in fc.pubs if t.endswith("/config")}
                assert not configs[f"{mqtt.DISCOVERY_PREFIX}/button/{mqtt.NODE}/sound_horn/config"]
                assert configs[f"{mqtt.DISCOVERY_PREFIX}/button/{mqtt.NODE}/flash_lights/config"]
        snapshots.append((dict(mqtt._MQTT_CTX, supported=set(mqtt._MQTT_CTX["supported"])),
                          json.loads((await main._panel_state(None)).text)))

    _main_cycles(monkeypatch, 3, after_cycle)
    assert all(panel["ok"] and panel["data"]["battery_level"] == 55 for _, panel in snapshots)
    configs = {t: (p, retain) for t, p, retain in fc.pubs if t.endswith("/config")}
    for domain, controls in (("button", (("sound_horn", "horn"), ("flash_lights", "lights"))),
                             ("number", (("soc_min_target", "soc_min_target"),
                                         ("soc_max_target", "soc_max_target")))):
        for short, cmd in controls:
            payload, retain = configs[f"{mqtt.DISCOVERY_PREFIX}/{domain}/{mqtt.NODE}/{short}/config"]
            assert payload, f"{cmd} discovery was not restored after {failure} recovery"
            assert json.loads(payload)["command_topic"] == f"{mqtt.CMD_PREFIX}{cmd}"
            assert retain is True
    ctx, panel = snapshots[-1]
    assert expected <= ctx["supported"]
    assert expected <= set(panel["supported"])
    assert probes["actions/charge-start"] == 1, "Definite false must not be probed every poll"
    assert "actions/charge-start" not in ctx["supported"]
    if failure == "probe":
        assert probes[main.SOC_ENDPOINT] >= 3, "Unknown support must be retried"
        assert all("actions/lights-start" in ctx["supported"] for ctx, _ in snapshots)


def test_timed_out_support_retry_keeps_resolved_answers_and_successful_poll(monkeypatch, wire_main):
    real_detect, real_discovery, real_poll = main.detect_supported, mqtt.publish_discovery, main.poll_once
    event_type = asyncio.Event
    probes, cancelled = {}, []

    class V(_ChargingVehicle):
        async def supports_endpoint(self, ep):
            probes[ep] = probes.get(ep, 0) + 1
            if ep in {"actions/horn-start", main.SOC_ENDPOINT} and probes[ep] == 1:
                raise RuntimeError("temporary probe outage")
            if ep == main.SOC_ENDPOINT:
                try:
                    await event_type().wait()
                finally:
                    cancelled.append(ep)
            return ep == "actions/horn-start"

    fc = wire_main(real_poll)
    monkeypatch.setattr(main, "poll_once", real_poll)
    monkeypatch.setattr(main, "VehicleSession", lambda locale: _Sess(V(), locale))
    monkeypatch.setattr(main, "detect_supported", real_detect)
    monkeypatch.setattr(mqtt, "publish_discovery", real_discovery)
    monkeypatch.setattr(main, "API_TIMEOUT", main.aiohttp.ClientTimeout(total=0.01))
    snapshots = []

    async def after_cycle(n):
        snapshots.append(json.loads((await main._panel_state(None)).text))

    _main_cycles(monkeypatch, 2, after_cycle)
    assert cancelled == [main.SOC_ENDPOINT, main.SOC_ENDPOINT]
    assert probes["actions/charge-start"] == 1
    assert probes["actions/horn-start"] == 2
    assert all(panel["ok"] and panel["data"]["battery_level"] == 55 for panel in snapshots)
    assert all("actions/horn-start" in panel["supported"] for panel in snapshots)
    assert "actions/horn-start" in mqtt._MQTT_CTX["supported"]
    configs = {t: p for t, p, _ in fc.pubs if t.endswith("/config")}
    assert configs[f"{mqtt.DISCOVERY_PREFIX}/button/{mqtt.NODE}/sound_horn/config"]


def test_main_retains_last_good_snapshot_through_failure_and_recovery(monkeypatch, wire_main):
    from renault_mqtt import freshness

    real_poll = main.poll_once
    base = 1767225600.0  # 2026-01-01T00:00:00Z
    clock, cycle = {"t": base}, {"n": 0}

    class V(_ChargingVehicle):
        async def get_battery_status(self):
            if cycle["n"] in (1, 2):
                raise RuntimeError("temporary battery outage")
            battery = _ChargingBattery()
            battery.timestamp = main.iso(base - 1800)
            if cycle["n"] == 3:
                battery.batteryLevel, battery.timestamp = 75, main.iso(clock["t"])
            return battery

        async def get_location(self):
            return _obj(gpsLatitude=51.5, gpsLongitude=-0.1,
                        lastUpdateTime=main.iso(base - 600 if cycle["n"] == 0 else clock["t"]))

    fc = wire_main(real_poll)
    monkeypatch.setattr(main, "poll_once", real_poll)
    monkeypatch.setattr(main, "VehicleSession", lambda locale: _Sess(V(), locale))
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    monkeypatch.setattr(freshness, "now_ts", lambda: clock["t"])
    monkeypatch.setenv("R5_STALE_HOURS", "1")
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    snapshots = []

    async def after_cycle(n):
        payload, retained = [(p, r) for t, p, r in fc.pubs if t == mqtt.STATE_TOPIC][-1]
        snapshots.append((json.loads(payload), json.loads((await main._panel_state(None)).text), retained))
        cycle["n"] += 1
        clock["t"] = base + (1801, 3601, 3660, 3720)[n]

    _main_cycles(monkeypatch, 4, after_cycle)
    good = snapshots[0][0]
    assert good["battery_level"] == 55
    assert good["battery_last_activity"] == main.iso(base - 1800)
    assert good["gps_last_activity"] == main.iso(base - 600)
    for n, (data, panel, retain) in enumerate(snapshots):
        assert retain is True
        assert data == panel["data"], f"MQTT and panel disagree on cycle {n}"
        assert panel["ok"] is (n in (0, 3))
        assert data["data_stale"] == ("on" if n in (1, 2) else "off")
        assert data["poll_failing"] == ("on" if n == 2 else "off")
        if n in (1, 2):
            for key, value in good.items():
                if key not in {"data_stale", "poll_failing", "api_auth_failure"}:
                    assert data[key] == value, f"Last-good {key} changed during failure"
    recovered = snapshots[-1][0]
    assert recovered["battery_level"] == 75
    assert recovered["battery_last_activity"] == recovered["gps_last_activity"] == main.iso(base + 3660)
    assert recovered["last_successful_poll"] == main.iso(base + 3660)


def test_main_failure_before_first_success_does_not_invent_vehicle_data(monkeypatch, wire_main):
    main._LATEST["data"] = {"battery_level": 99, "gps_last_activity": "another vehicle's snapshot"}
    published, invalidated = _one_failed_poll(monkeypatch, wire_main, RuntimeError("service unavailable"))
    assert len(published) == 1 and invalidated == 0
    data = published[0]
    assert data == main._LATEST["data"]
    assert data["poll_failing"] == "on" and data["api_auth_failure"] == "off"
    assert not data.get("last_successful_poll")
    assert {"battery_level", "battery_last_activity", "gps_last_activity", "data_stale"}.isdisjoint(data)


@pytest.mark.parametrize("first_failure", [False, True])
def test_main_delayed_connect_delivers_latest_poll_without_another_poll(monkeypatch, wire_main, first_failure):
    cycle = {"n": 0}
    location = {"latitude": 1, "longitude": 2}

    async def poll(*args):
        if first_failure or cycle["n"] == 1:
            raise RuntimeError("temporary outage")
        return {"battery_level": 50, "battery_last_activity": "2026-01-01T00:00:00Z"}, location

    fc = wire_main(poll)
    monkeypatch.setattr(main, "poll_once", poll)
    fc.connected = False
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)

    async def after_cycle(n):
        assert not [p for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC]
        if n == 1:
            fc.connect()
            data = json.loads([p for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC][-1])
            assert data == main._LATEST["data"]
            assert main._LATEST["ok"] is False
            if first_failure:
                assert "battery_level" not in data and fc.location is None
            else:
                assert data["battery_level"] == 50
                assert data["battery_last_activity"] == "2026-01-01T00:00:00Z"
                assert json.loads(fc.location) == location
        cycle["n"] += 1

    _main_cycles(monkeypatch, 2, after_cycle)
    assert fc.closed and fc.disconnected and fc.stopped
    assert fc.pubs[-1] == (mqtt.AVAIL_TOPIC, "offline", True)


@pytest.mark.parametrize("location_enabled", [False, True])
def test_main_snapshot_location_no_fix_and_optout(monkeypatch, wire_main, location_enabled):
    real_poll = main.poll_once
    cycle = {"n": 0}

    class V(_ChargingVehicle):
        async def get_location(self):
            assert location_enabled, "Privacy opt-out must not fetch coordinates"
            if cycle["n"]:
                raise RuntimeError("no new fix")
            return _obj(gpsLatitude=1, gpsLongitude=2, lastUpdateTime="2026-01-01T00:00:00Z")

    fc = wire_main(real_poll)
    monkeypatch.setattr(main, "poll_once", real_poll)
    fc.connected = False
    monkeypatch.setattr(main, "VehicleSession", lambda locale: _Sess(V(), locale))
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", location_enabled)
    fixes = []

    async def after_cycle(n):
        fixes.append(fc.location)
        if n == 1:
            fc.connect()
        cycle["n"] += 1

    _main_cycles(monkeypatch, 2, after_cycle)
    assert fixes[0] == fixes[1]
    attrs = [p for t, p, _ in fc.pubs if t == mqtt.ATTR_TOPIC]
    assert attrs == [fixes[0]]
    if location_enabled:
        assert set(json.loads(attrs[0])) >= {"latitude", "longitude"}
    else:
        assert attrs == [""]


def test_main_runs_one_successful_cycle(wire_main):
    async def poll(stop, *a, **k):
        stop.set()                                # end the loop after this iteration
        return ({"battery_level": 80, "charger_plug_status": "Connected",
                 "charging": "on", "plug_suspect": "off"},
                {"latitude": 51.5, "longitude": -0.1})

    fc = wire_main(poll)
    asyncio.run(main.main())
    topics = [t for t, _, _ in fc.pubs]
    assert mqtt.STATE_TOPIC in topics
    assert mqtt.ATTR_TOPIC in topics              # location attrs published
    # ...and NOTHING on the tracker state topic: a state payload becomes location_name, which
    # outranks the lat/lon attributes and pins the tracker to it. This assertion used to require
    # the write that caused the bug, so it now guards the fix.
    assert mqtt.TRACKER_STATE_TOPIC not in topics
    assert [(p, r) for t, p, r in fc.pubs if t == mqtt.AVAIL_TOPIC] == [
        ("online", True), ("offline", True)]
    for topic in (mqtt.STATE_TOPIC, mqtt.ATTR_TOPIC):
        assert all(r is True for t, _, r in fc.pubs if t == topic)
    assert fc.stopped is True and fc.disconnected is True
    assert main.load_state()["last_success"] > 0


@pytest.mark.parametrize("publish_location,enable_refresh,expected", [
    (True, False, False), (True, True, True), (False, True, False), (None, None, False)])
def test_main_tells_deploy_whether_the_refresh_button_exists(
        monkeypatch, wire_main, publish_location, enable_refresh, expected):
    """deploy keeps the Refresh Location tile only on this verdict, so it must be the core's own
    publish condition. None is an unconfigured core: withhold, the safe direction."""
    async def poll(stop, *a, **k):
        stop.set()
        return ({"battery_level": 80}, None)

    fc = wire_main(poll)
    monkeypatch.setattr(mqtt, "configure", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", publish_location)
    monkeypatch.setattr(mqtt, "ENABLE_REFRESH_LOCATION", enable_refresh)
    asyncio.run(main.main())
    assert fc.deploy_calls == [{"refresh_location": expected}]


def test_main_failure_branch_flags_auth_and_staleness(wire_main):
    async def poll(stop, *a, **k):
        stop.set()
        raise RuntimeError("HTTP 401 invalid credentials")

    fc = wire_main(poll)
    asyncio.run(main.main())
    # A failing poll still persists state: the endpoint probe and the breaker write into it, and a
    # restart would otherwise re-probe everything the car just refused.
    assert Path(main.STATE_FILE).is_file(), "the failure branch must save state"
    state_payloads = [json.loads(p) for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC]
    assert all(r is True for t, _, r in fc.pubs if t == mqtt.STATE_TOPIC)
    assert any(d.get("api_auth_failure") == "on" for d in state_payloads)
    # poll_failing, not data_stale: the split separates "cannot reach Kamereon" from "the car has
    # not reported". This process never polled successfully, so poll_failing is on, and data_stale
    # is omitted: no car timestamp was seen, and "off" would assert freshness we cannot know.
    assert any(d.get("poll_failing") == "on" for d in state_payloads)
    assert all("data_stale" not in d for d in state_payloads)


def _skip_real_sleeps(monkeypatch):
    """The inter-poll backoff sleep is real seconds otherwise (up to the 30-minute cap) —
    short-circuit it the same way test_failure_backoff_never_drops_below_the_interval does."""
    real_wait_for = asyncio.wait_for

    async def fake_wait_for(aw, timeout):
        if getattr(aw, "__qualname__", "") == "Event.wait":
            aw.close()
            raise asyncio.TimeoutError
        return await real_wait_for(aw, timeout)

    monkeypatch.setattr(main.asyncio, "wait_for", fake_wait_for)


def test_relogin_only_on_auth_error_or_every_third_failure(monkeypatch, wire_main):
    """a290 twin's policy: invalidating on every failure re-authenticated against transient
    network blips too, so only a confirmed auth error or every 3rd failure re-logs in."""
    calls = {"n": 0}

    async def poll(stop, *a, **k):
        calls["n"] += 1
        if calls["n"] == 5:
            stop.set()
        raise RuntimeError("Kamereon unreachable")   # never auth-flavoured

    fc = wire_main(poll)
    _skip_real_sleeps(monkeypatch)

    class SpySession:
        def __init__(self, locale):
            self.invalidations = []

        async def invalidate(self):
            self.invalidations.append(calls["n"])

        async def close(self):
            pass

    spy = {}

    def make_spy(locale):
        s = SpySession(locale)
        spy["session"] = s
        return s

    monkeypatch.setattr(main, "VehicleSession", make_spy)
    asyncio.run(main.main())
    assert spy["session"].invalidations == [3]   # 1st and 2nd skipped, 3rd re-logs in
    assert len(fc.pubs) > 0   # sanity: the loop actually ran


def test_relogin_on_every_auth_failure(monkeypatch, wire_main):
    calls = {"n": 0}

    async def poll(stop, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            stop.set()
        raise RuntimeError("HTTP 401 invalid credentials")

    fc = wire_main(poll)
    _skip_real_sleeps(monkeypatch)

    class SpySession:
        def __init__(self, locale):
            self.invalidations = []

        async def invalidate(self):
            self.invalidations.append(calls["n"])

        async def close(self):
            pass

    spy = {}

    def make_spy(locale):
        s = SpySession(locale)
        spy["session"] = s
        return s

    monkeypatch.setattr(main, "VehicleSession", make_spy)
    asyncio.run(main.main())
    assert spy["session"].invalidations == [1, 2]   # every failure, not just every 3rd
    assert len(fc.pubs) > 0


def _one_failed_poll(monkeypatch, wire_main, err):
    """main() through a single poll that raises `err`: the state documents it published, and how
    many times the Renault session was thrown away (invalidate) on that one failure."""
    async def poll(stop, *a, **k):
        stop.set()
        raise err

    fc = wire_main(poll)
    seen = {"invalidated": 0}

    class CountingVS:
        def __init__(self, locale):
            self.locale = locale

        async def invalidate(self):
            seen["invalidated"] += 1

        async def close(self):
            pass

    monkeypatch.setattr(main, "VehicleSession", CountingVS)
    asyncio.run(main.main())
    return [json.loads(p) for t, p, _ in fc.pubs if t == mqtt.STATE_TOPIC], seen["invalidated"]


@pytest.mark.parametrize("err", [
    pytest.param(UnauthorizedException("err.func.wired.unauthorized", "Not authorized"), id="kamereon"),
    pytest.param(NotAuthenticatedException("Authentication expired."), id="expired-session"),
])
def test_kamereon_unauthorized_is_an_auth_failure(monkeypatch, wire_main, err):
    # str() is "('err.func.wired.unauthorized', 'Not authorized')", which no keyword match catches,
    # so it read as an ordinary failure. The gap predates 0.5.14, which only gives the reply its
    # own class — and that is what lets it be recognised by type.
    published, invalidated = _one_failed_poll(monkeypatch, wire_main, err)
    assert published and all(d["api_auth_failure"] == "on" for d in published)
    assert main._LATEST["data"]["api_auth_failure"] == "on"
    assert invalidated == 1        # a rejected token is dropped at once, not on every third failure



def test_two_factor_account_is_an_auth_failure(monkeypatch, wire_main):
    # Recognised by type: its text "(403101, ...)" matched the keyword list only by the accident of
    # containing "403". The detail string here deliberately avoids every keyword.
    err = PendingTwoFactorAuthenticationException(403101, "pending verification", reg_token="r")
    err.args = ("pending verification",)
    published, invalidated = _one_failed_poll(monkeypatch, wire_main, err)
    assert published and all(d["api_auth_failure"] == "on" for d in published)
    assert main._LATEST["data"]["api_auth_failure"] == "on"
    assert invalidated == 1


def test_other_kamereon_errors_are_not_auth_failures(monkeypatch, wire_main):
    # The type check must not widen into "any Kamereon error": a bad request is not a credentials
    # problem, the sensor stays off, and the session survives the first failure.
    err = InvalidInputException("err.func.wired.invalid-body-format", "bad body")
    published, invalidated = _one_failed_poll(monkeypatch, wire_main, err)
    assert published and all(d["api_auth_failure"] == "off" for d in published)
    assert invalidated == 0


def test_main_exits_without_required_config(monkeypatch):
    for k in ("R5_USERNAME", "R5_PASSWORD", "R5_VIN", "MQTT_HOST"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        asyncio.run(main.main())


@pytest.mark.parametrize("interval,expected", [
    (3600, [3600, 3600, 3600, 3600]),        # a slow interval must not retry faster when failing
    (300, [300, 600, 1200, 1800, 1800]),     # a fast one still backs off to the 30-minute cap
])
def test_failure_backoff_never_drops_below_the_interval(monkeypatch, wire_main, interval, expected):
    polls = {"n": 0}

    async def poll(stop, *a, **k):
        polls["n"] += 1
        if polls["n"] == len(expected):
            stop.set()
        raise RuntimeError("Kamereon unreachable")

    wire_main(poll)
    monkeypatch.setenv("R5_POLL_INTERVAL", str(interval))
    delays, real_wait_for = [], asyncio.wait_for

    async def fake_wait_for(aw, timeout):
        if getattr(aw, "__qualname__", "") == "Event.wait":   # the inter-poll sleep, not the poll
            delays.append(timeout)
            aw.close()
            raise asyncio.TimeoutError
        return await real_wait_for(aw, timeout)

    monkeypatch.setattr(main.asyncio, "wait_for", fake_wait_for)
    asyncio.run(main.main())
    assert delays == expected


# --------------------------------------------------------------------------- #
# every Renault API session is bounded, so a hung connection can't wedge a poll or command
# --------------------------------------------------------------------------- #
class _TimedVehicle:
    async def start_horn(self):
        pass

    async def get_battery_soc(self):
        return _obj(socMin=20, socTarget=80)

    async def set_battery_soc(self, min, target):
        pass


def _poll_session():
    asyncio.run(main.VehicleSession("en_GB").vehicle())


def _command_session():
    asyncio.run(main.run_command("horn"))


def _soc_session():
    asyncio.run(main.set_soc_level("soc_min_target", "30"))


SESSION_PATHS = {"poll": _poll_session, "command": _command_session, "charge_limit": _soc_session}


@pytest.mark.parametrize("path", sorted(SESSION_PATHS))
def test_renault_session_is_created_with_the_api_timeout(monkeypatch, path):
    made = []

    class _S(_CmdSession):
        async def close(self):
            pass

    def factory(*a, **k):
        made.append(k)
        return _S()

    async def login(ws, locale):
        return _TimedVehicle()

    monkeypatch.setattr(main.aiohttp, "ClientSession", factory)
    monkeypatch.setattr(main, "_login_vehicle", login)
    SESSION_PATHS[path]()
    assert len(made) == 1
    timeout = made[0].get("timeout")
    assert isinstance(timeout, main.aiohttp.ClientTimeout), made[0]
    assert (timeout.total, timeout.connect) == (60, 10)


def test_no_client_session_in_main_escapes_the_timeout_tests():
    """A new session in main.py fails here until it passes a timeout and gets a SESSION_PATHS entry."""
    tree = ast.parse(Path(main.__file__).read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "ClientSession"]
    assert len(calls) == len(SESSION_PATHS)
    assert all(any(kw.arg == "timeout" for kw in c.keywords) for c in calls)


# --------------------------------------------------------------------------- #
# deploy.py — local dashboard read + WebSocket deploy
# --------------------------------------------------------------------------- #


class _FakeWS:
    """Scripts the HA WebSocket: auth handshake then result frames keyed on the
    last command type sent."""

    def __init__(self, dashboards=(), resources=()):
        self._last = None
        self.sent = []
        self.saved = None
        self.created_dashboard = None
        self.created_resource = None
        self.created_paths = []
        self.saved_paths = []
        self._dashboards = list(dashboards)
        self._resources = list(resources)
        self._auth_ok_type = "auth_ok"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send_json(self, payload):
        self._last = payload
        self.sent.append(payload)

    async def receive_json(self):
        if self._last is None:
            return {"type": "auth_required"}
        t = self._last.get("type")
        if t == "auth":
            return {"type": self._auth_ok_type}
        result = None
        if t == "lovelace/resources":
            result = self._resources
        elif t == "lovelace/resources/create":
            self.created_resource = self._last
            result = {"id": "res"}
        elif t == "lovelace/dashboards/list":
            result = self._dashboards
        elif t == "lovelace/dashboards/create":
            self.created_dashboard = self._last
            self.created_paths.append(self._last.get("url_path"))
            result = {"id": "dash"}
        elif t == "lovelace/config/save":
            self.saved = self._last
            self.saved_paths.append(self._last.get("url_path"))
        return {"id": self._last.get("id"), "type": "result", "success": True, "result": result}


class _FakeCS:
    """Stands in for aiohttp.ClientSession for the WS connect (the dashboard YAML is now
    read locally from DASHBOARD_DIR, so no HTTP fetch is mocked)."""
    ws = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def ws_connect(self, url, timeout=None):
        return _FakeCS.ws


_DASH_YAML = (
    "- title: Main\n"
    "  cards:\n"
    "    - image: /local/backgrounds/r5_background.webp\n"
    "    - image: /local/backgrounds/r5_side.webp\n"
    "    - image: /local/backgrounds/charge-indicator.png\n"
    "    - image: /local/backgrounds/unmapped.png\n"
)


def _deploy_env(monkeypatch, **over):
    base = {"R5_DEPLOY_DASHBOARD": "standard", "SUPERVISOR_TOKEN": "tok",
            "R5_DASHBOARD_URL_PATH": "renault-5"}
    base.update(over)
    for k, v in base.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    monkeypatch.delenv("R5_REDEPLOY_DASHBOARD", raising=False)
    monkeypatch.delenv("R5_CAR_RENDER", raising=False)
    # Dashboards are bundled in the image and read locally — point DASHBOARD_DIR at a temp
    # copy of the fixture YAML (both styles) so _fetch_dashboard reads it without a network.
    d = tempfile.mkdtemp()
    for fname in deploy.DASHBOARDS.values():
        with open(os.path.join(d, fname), "w", encoding="utf-8") as fh:
            fh.write(_DASH_YAML)
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", d)


def test_run_deploy_creates_dashboard_and_rewrites_assets(monkeypatch):
    _deploy_env(monkeypatch)
    ws = _FakeWS(dashboards=[], resources=[])
    _FakeCS.ws = ws
    monkeypatch.setattr(deploy.aiohttp, "ClientSession", _FakeCS)
    asyncio.run(deploy.run_deploy())
    assert ws.created_resource is not None          # font registered (none existed)
    assert ws.created_dashboard is not None          # url_path was absent => created
    assert ws.saved is not None
    txt = json.dumps(ws.saved["config"])
    assert "cdn.jsdelivr.net/gh/MatthewHobbs/r5-ha-addon@main/renault_5/dashboards/Images/Background/r5_background.webp" in txt
    assert "/local/backgrounds/unmapped.png" in txt  # unmapped reference left untouched


# dashboard_url_path validation — never overwrite a built-in HA panel
def test_validate_url_path_normalises_valid():
    assert deploy._validate_url_path("  Renault-5  ") == "renault-5"


@pytest.mark.parametrize("bad", ["", "renault_5", "energy", "developer-tools", "no space", "UPPER"])
def test_validate_url_path_rejects_bad(bad):
    with pytest.raises(ValueError):
        deploy._validate_url_path(bad)


def _record_sessions(monkeypatch):
    # run_deploy swallows every error, so "nothing happened" is only visible as "no session opened".
    opened = []
    monkeypatch.setattr(deploy.aiohttp, "ClientSession", lambda *a, **k: opened.append(1))
    return opened


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# _deploy_env sets a token, so in each early-exit test below deleting the guard under test reaches
# the session instead of stopping at a later guard.
def test_run_deploy_skips_reserved_url_path(monkeypatch, caplog):
    # Must return before any WS connection / save: deploying to "energy" would overwrite a built-in
    # HA panel.
    _deploy_env(monkeypatch, R5_DASHBOARD_URL_PATH="energy")
    opened = _record_sessions(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(deploy.run_deploy())
    assert opened == []
    assert any("auto-deploy skipped" in m for m in _warnings(caplog))


def test_deploy_targets():
    assert deploy._deploy_targets("standard", "renault-5") == [("standard", "renault-5", "Renault 5")]
    assert deploy._deploy_targets("bubble", "renault-5") == [("bubble", "renault-5", "Renault 5")]
    assert deploy._deploy_targets("both", "renault-5") == [
        ("standard", "renault-5", "Renault 5"),
        ("bubble", "renault-5-bubble", "Renault 5 (Bubble)"),
    ]


def test_run_deploy_both_installs_two_dashboards(monkeypatch):
    _deploy_env(monkeypatch, R5_DEPLOY_DASHBOARD="both")
    ws = _FakeWS(dashboards=[], resources=[])
    _FakeCS.ws = ws
    monkeypatch.setattr(deploy.aiohttp, "ClientSession", _FakeCS)
    asyncio.run(deploy.run_deploy())
    # standard at the configured path, bubble at <path>-bubble
    assert ws.created_paths == ["renault-5", "renault-5-bubble"]
    assert ws.saved_paths == ["renault-5", "renault-5-bubble"]
    assert ws.created_resource is not None          # font registered once for the run


def test_run_deploy_render_override_and_redeploy(monkeypatch):
    _deploy_env(monkeypatch)
    monkeypatch.setenv("R5_REDEPLOY_DASHBOARD", "true")
    monkeypatch.setenv("R5_CAR_RENDER", "midnight-blue-iconic")
    ws = _FakeWS(dashboards=[{"url_path": "renault-5"}], resources=[])
    _FakeCS.ws = ws
    monkeypatch.setattr(deploy.aiohttp, "ClientSession", _FakeCS)
    asyncio.run(deploy.run_deploy())
    assert ws.created_dashboard is None              # already existed => not recreated
    assert ws.saved is not None                      # redeploy overwrote config
    txt = json.dumps(ws.saved["config"])
    assert "Images/Models/Iconic/midnight-blue-iconic.webp" in txt   # render override applied


def test_run_deploy_leaves_existing_dashboard_alone(monkeypatch):
    _deploy_env(monkeypatch)
    ws = _FakeWS(dashboards=[{"url_path": "renault-5"}], resources=[{"url": deploy.FONT_URL}])
    _FakeCS.ws = ws
    monkeypatch.setattr(deploy.aiohttp, "ClientSession", _FakeCS)
    asyncio.run(deploy.run_deploy())
    assert ws.created_resource is None               # font already present => skipped
    assert ws.created_dashboard is None and ws.saved is None   # left intact (no redeploy)


def test_run_deploy_noop_when_disabled(monkeypatch, caplog):
    _deploy_env(monkeypatch, R5_DEPLOY_DASHBOARD="none")
    opened = _record_sessions(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(deploy.run_deploy())
    assert opened == []
    assert _warnings(caplog) == []     # "none" is a choice, not an unrecognised style


def test_run_deploy_skips_unknown_style(monkeypatch, caplog):
    _deploy_env(monkeypatch, R5_DEPLOY_DASHBOARD="fancy")
    opened = _record_sessions(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(deploy.run_deploy())
    assert opened == []
    assert any("'fancy' not recognised" in m for m in _warnings(caplog))


def test_run_deploy_skips_without_supervisor_token(monkeypatch, caplog):
    _deploy_env(monkeypatch, SUPERVISOR_TOKEN=None)
    opened = _record_sessions(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(deploy.run_deploy())
    assert opened == []
    assert any("No SUPERVISOR_TOKEN" in m for m in _warnings(caplog))


def test_run_deploy_swallows_runtime_errors(monkeypatch):
    _deploy_env(monkeypatch)
    ws = _FakeWS()
    ws._auth_ok_type = "auth_invalid"                # auth handshake fails
    _FakeCS.ws = ws
    monkeypatch.setattr(deploy.aiohttp, "ClientSession", _FakeCS)
    asyncio.run(deploy.run_deploy())                 # error is caught; poller must survive
    assert ws.saved is None


def test_fetch_dashboard_rejects_non_list(monkeypatch, tmp_path):
    (tmp_path / "front-end.txt").write_text("title: not-a-list\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        asyncio.run(deploy._fetch_dashboard("standard"))


# (Smart Charging deploy tests moved to test_deploy.py — they cover the Mushroom standard
# block + bubble pop-up that replaced the old plain entities card.)


def test_ws_auth_then_commands():
    ws = _FakeWS(dashboards=[{"url_path": "renault-5"}], resources=[{"id": "font-1"}])
    config = {"views": [{"title": "Home", "cards": []}]}

    async def scenario():
        api = deploy._WS(ws, "token")
        await api.auth()
        results = [await api.dashboards(),
                   await api.create_dashboard("renault-5", "Renault 5"),
                   await api.save_config("renault-5", config),
                   await api.resources(),
                   await api.create_resource("https://font")]
        assert results == [[{"url_path": "renault-5"}], {"id": "dash"}, None,
                           [{"id": "font-1"}], {"id": "res"}]

    asyncio.run(scenario())
    assert ws.sent == [
        {"type": "auth", "access_token": "token"},
        {"id": 1, "type": "lovelace/dashboards/list"},
        {"id": 2, "type": "lovelace/dashboards/create", "url_path": "renault-5",
         "title": "Renault 5", "icon": "mdi:car-sports", "mode": "storage",
         "show_in_sidebar": True, "require_admin": False},
        {"id": 3, "type": "lovelace/config/save", "url_path": "renault-5", "config": config},
        {"id": 4, "type": "lovelace/resources"},
        {"id": 5, "type": "lovelace/resources/create", "url": "https://font", "res_type": "css"},
    ]


def test_ws_cmd_raises_on_unsuccessful_result():
    class WS:
        async def send_json(self, payload):
            self._last = payload

        async def receive_json(self):
            return {"id": self._last["id"], "type": "result",
                    "success": False, "error": {"message": "denied"}}

    api = deploy._WS(WS(), "tok")
    with pytest.raises(RuntimeError):
        asyncio.run(api.dashboards())


def test_detect_supported_tolerates_action_probe_error():
    class V:
        async def supports_endpoint(self, ep):
            if ep == "actions/horn-start":
                raise RuntimeError("probe blew up")
            return True

    class Sess:
        async def vehicle(self):
            return V()

        async def invalidate(self):
            pass

    sup = asyncio.run(main.detect_supported(Sess()))
    assert "actions/lights-start" in sup          # other actions still detected
    assert "actions/horn-start" not in sup        # the one whose probe errored is omitted


# --------------------------------------------------------------------------- #
# error-snapshot redaction + _LATEST GPS carry-forward + hvac-settings breaker
# --------------------------------------------------------------------------- #
def test_main_redacts_secret_in_error_snapshot(monkeypatch, wire_main):
    async def poll(stop, *a, **k):
        stop.set()                             # end the loop after this iteration
        # an error carrying the VIN (as a real Kamereon URL error would)
        raise RuntimeError("403 at https://api/accounts/A/vehicles/SECRETVIN123/charges")

    wire_main(poll)
    monkeypatch.setenv("R5_VIN", "SECRETVIN123")
    asyncio.run(main.main())
    # the status-panel snapshot (served unauth to co-tenant containers) must not carry the VIN
    assert "SECRETVIN123" not in main._LATEST.get("error", "")
    assert "***" in main._LATEST.get("error", "")


class _SentinelVehicle(_ChargingVehicle):
    async def get_location(self):
        return _obj(gpsLatitude=91, gpsLongitude=181, lastUpdateTime="2026-09-07T10:00:00Z")


def test_sentinel_fix_falls_back_to_the_last_published_timestamp():
    """First poll after upgrading has no persisted value; _LATEST covers that window."""
    main._LATEST["data"] = {"gps_last_activity": "2026-09-02T09:00:00Z"}
    try:
        data, _ = asyncio.run(main.poll_once(_Sess(_SentinelVehicle()), {}, 52.0, set(), "km"))
        assert data["gps_last_activity"] == "2026-09-02T09:00:00Z"
    finally:
        main._LATEST["data"] = {}


def test_hvac_breaker_real_poll_trip_retry_recovery_and_bounded_logging(caplog):
    """Real poll_once wires BREAKER_TRIP, BREAKER_RETRY_EVERY, _hvac_due, breaker state,
    bounded warning/recovery logs, and _hvac_schedule_fields across pre-trip reset, trip,
    silent skipped polls, silent failed retry probe, and retry probe recovery."""
    assert (main.BREAKER_TRIP, main.BREAKER_RETRY_EVERY) == (3, 12)
    calls = []
    broken = {"v": True}

    class _V(_ChargingVehicle):
        async def get_hvac_settings(self):
            calls.append(1)
            if broken["v"]:
                raise RuntimeError("errorCode 502000 something went wrong")
            return await super().get_hvac_settings()

    eps = {"hvac-settings"}
    # Pre-trip failure then immediate recovery resets _BREAKER_FAILS without tripping.
    asyncio.run(main.poll_once(_Sess(_V()), {}, 52.0, eps, "km"))
    assert main._BREAKER_FAILS["hvac-settings"] == 1
    assert not main._BREAKERS.get("hvac-settings")
    broken["v"] = False
    data, _ = asyncio.run(main.poll_once(_Sess(_V()), {}, 52.0, eps, "km"))
    assert main._BREAKER_FAILS["hvac-settings"] == 0
    assert not main._BREAKERS.get("hvac-settings")
    assert data["climate_schedule_mode"] == "Scheduled"
    assert data["climate_ready_time"] == "Mon 07:15"

    # Trip after BREAKER_TRIP consecutive failures; failed periodic probe stays tripped and silent;
    # subsequent periodic probe recovers, logs INFO, and clears breaker state.
    calls.clear()
    caplog.clear()
    broken["v"] = True
    with caplog.at_level(logging.INFO):
        for _ in range(main.BREAKER_TRIP):
            data, _ = asyncio.run(main.poll_once(_Sess(_V()), {}, 52.0, eps, "km"))
            assert "climate_schedule_mode" not in data and "climate_ready_time" not in data
        assert len(calls) == main.BREAKER_TRIP
        assert main._BREAKERS.get("hvac-settings") is True
        assert main._BREAKER_FAILS["hvac-settings"] == main.BREAKER_TRIP

        # Skipped for BREAKER_RETRY_EVERY - 1 polls while tripped.
        for expected_skip in range(1, main.BREAKER_RETRY_EVERY):
            asyncio.run(main.poll_once(_Sess(_V()), {}, 52.0, eps, "km"))
            assert len(calls) == main.BREAKER_TRIP
            assert main._BREAKER_SKIPS["hvac-settings"] == expected_skip

        # Failed retry probe on the BREAKER_RETRY_EVERY-th poll stays tripped and silent.
        asyncio.run(main.poll_once(_Sess(_V()), {}, 52.0, eps, "km"))
        assert len(calls) == main.BREAKER_TRIP + 1
        assert main._BREAKERS.get("hvac-settings") is True
        assert main._BREAKER_SKIPS["hvac-settings"] == 0

        # Another BREAKER_RETRY_EVERY - 1 skipped polls, then recovery probe succeeds.
        for _ in range(main.BREAKER_RETRY_EVERY - 1):
            asyncio.run(main.poll_once(_Sess(_V()), {}, 52.0, eps, "km"))
        assert len(calls) == main.BREAKER_TRIP + 1
        broken["v"] = False
        data, _ = asyncio.run(main.poll_once(_Sess(_V()), {}, 52.0, eps, "km"))

    assert len(calls) == main.BREAKER_TRIP + 2
    assert "hvac-settings" not in main._BREAKERS
    assert main._BREAKER_FAILS["hvac-settings"] == 0
    assert main._BREAKER_SKIPS["hvac-settings"] == 0
    assert data["climate_schedule_mode"] == "Scheduled"
    assert data["climate_ready_time"] == "Mon 07:15"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "hvac-settings" in r.getMessage()]
    assert len(warnings) == main.BREAKER_TRIP
    assert "pausing it" in warnings[-1]
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "hvac-settings" in r.getMessage()]
    assert infos == ["hvac-settings is answering again - re-enabled."]


def test_the_climate_sensors_are_availability_gated_not_blanked():
    """The hvac-settings breaker stops writing these keys; without the gating they render as
    empty strings, which is what three releases of breaker work left behind."""
    import catalog
    assert catalog.DATA_GATED_SENSORS == {"r5_climate_schedule_mode", "r5_climate_ready_time"}
    for obj in catalog.DATA_GATED_SENSORS:
        assert obj in catalog.SENSORS, f"{obj} gated but not published"


# --------------------------------------------------------------------------- #
# state file shape and a cancelled login
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("document", ["[]", '"text"', "3", "null", "{}"])
def test_load_state_ignores_a_document_of_the_wrong_shape(tmp_path, monkeypatch, document):
    """The poll's own error path calls .get() on this, so a list would raise inside the
    handler and take the loop down."""
    path = tmp_path / "state.json"
    path.write_text(document)
    monkeypatch.setattr(main, "STATE_FILE", str(path))
    state = main.load_state()
    assert state == {}
    assert isinstance(state, dict)
