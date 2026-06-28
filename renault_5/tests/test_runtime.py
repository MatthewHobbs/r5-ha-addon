"""Runtime-path tests for the Renault 5 poller and dashboard deployer.

Complements test_main.py (pure helpers / discovery contract) by exercising the
side-effecting paths: the MQTT plumbing and callbacks, the cached-login failure
modes, command dispatch, the debug dump, account resolution, the main() poll
loop (success and failure), the extra poll_once endpoint branches, and the whole
deploy.py WebSocket flow. Tests stay synchronous and drive coroutines with
asyncio.run (so the suite needs only pytest, not pytest-asyncio).
"""
import asyncio
import json
import logging
import os
import tempfile

import deploy
import main
import pytest
from renault_api.kamereon.enums import ChargeState, PlugState


def _obj(**attrs):
    return type("Obj", (), attrs)()


# --------------------------------------------------------------------------- #
# tiny helpers
# --------------------------------------------------------------------------- #
def test_now_ts_and_iso():
    assert isinstance(main.now_ts(), float)
    assert main.iso(0) is None
    assert main.iso(1000).startswith("1970-01-01")


def test_setup_logging_runs(monkeypatch):
    monkeypatch.setenv("R5_LOG_LEVEL", "debug")
    main.setup_logging()        # exercises the level lookup; basicConfig is a no-op under pytest
    monkeypatch.setenv("R5_LOG_LEVEL", "nonsense")
    main.setup_logging()        # invalid level falls back to INFO


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
        self.pubs.append((topic, payload))

    def connect(self, host, port, keepalive=60):
        self.conn = (host, port, keepalive)

    def loop_start(self):
        self.started = True

    def loop_stop(self):
        self.stopped = True

    def disconnect(self):
        self.disconnected = True


def test_on_message_dispatches(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "_LOOP", object())
    monkeypatch.setattr(main.asyncio, "run_coroutine_threadsafe",
                        lambda coro, loop: captured.update(coro=coro, loop=loop))
    monkeypatch.setattr(main, "run_command", lambda cmd, payload="": ("CO", cmd, payload))
    main._on_message(None, None, _obj(topic=main.CMD_PREFIX + "horn", payload=b""))
    assert captured["coro"] == ("CO", "horn", "")
    main._on_message(None, None, _obj(topic=main.CMD_PREFIX + "soc_max_target", payload=b"90"))
    assert captured["coro"] == ("CO", "soc_max_target", "90")


def test_on_message_ignores_non_command_and_no_loop(monkeypatch):
    fired = {"n": 0}
    monkeypatch.setattr(main.asyncio, "run_coroutine_threadsafe",
                        lambda *a, **k: fired.__setitem__("n", fired["n"] + 1))
    monkeypatch.setattr(main, "_LOOP", object())
    main._on_message(None, None, _obj(topic="some/other/topic"))    # wrong prefix
    monkeypatch.setattr(main, "_LOOP", None)
    main._on_message(None, None, _obj(topic=main.CMD_PREFIX + "horn"))  # loop not ready
    assert fired["n"] == 0


def test_on_connect_subscribes_and_announces():
    c = _FakeClient()
    main._on_connect(c, None, None, 0)
    assert c.subs == [f"{main.CMD_PREFIX}#"]
    assert (main.AVAIL_TOPIC, "online") in c.pubs
    refused = _FakeClient()
    main._on_connect(refused, None, None, 5)
    assert refused.subs == [] and refused.pubs == []


def test_on_disconnect_logs_both_paths():
    main._on_disconnect(None, None, None, 0)   # clean
    main._on_disconnect(None, None, None, 1)   # unexpected (warns)


def test_mqtt_connect(monkeypatch):
    holder = {}

    def factory(*a, **k):
        c = _FakeClient(*a, **k)
        holder["c"] = c
        return c

    monkeypatch.setattr(main.mqtt, "Client", factory)
    monkeypatch.setenv("MQTT_USER", "u")
    monkeypatch.setenv("MQTT_PASS", "p")
    monkeypatch.setenv("MQTT_HOST", "broker")
    monkeypatch.setenv("MQTT_PORT", "1884")
    client = main.mqtt_connect()
    c = holder["c"]
    assert client is c
    assert c.creds == ("u", "p")
    assert c.conn == ("broker", 1884, 30)
    assert c.delay == {"min_delay": 1, "max_delay": 120}
    assert c.started is True


# --------------------------------------------------------------------------- #
# cached login session — failure modes
# --------------------------------------------------------------------------- #
def test_vehicle_session_invalidates_on_login_failure(monkeypatch):
    closed = {"n": 0}

    class FakeSession:
        async def close(self):
            closed["n"] += 1

    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: FakeSession())

    async def boom(ws, loc):
        raise RuntimeError("login refused")

    monkeypatch.setattr(main, "_login_vehicle", boom)

    async def scenario():
        vs = main.VehicleSession("en_GB")
        with pytest.raises(RuntimeError):
            await vs.vehicle()
        assert closed["n"] == 1          # half-open session was closed
        assert vs._vehicle is None

    asyncio.run(scenario())


def test_invalidate_swallows_close_error():
    class BadSession:
        async def close(self):
            raise RuntimeError("already closed")

    async def scenario():
        vs = main.VehicleSession("en_GB")
        vs._websession = BadSession()
        await vs.invalidate()            # must not raise
        assert vs._websession is None

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# endpoint-support detection
# --------------------------------------------------------------------------- #
def test_supports_handles_sync_and_async():
    class SyncV:
        def supports_endpoint(self, ep):
            return True

    class AsyncV:
        def supports_endpoint(self, ep):
            async def _a():
                return True
            return _a()

    async def scenario():
        assert await main._supports(SyncV(), "x") is True
        assert await main._supports(AsyncV(), "x") is True

    asyncio.run(scenario())


def test_detect_supported_probes_endpoints():
    actions = {ep for *_rest, ep in main.ACTION_BUTTONS.values()}

    class V:
        def supports_endpoint(self, ep):
            return ep != "pressure"      # pressure unsupported; charge-mode + all actions ok

    class Sess:
        async def vehicle(self):
            return V()

        async def invalidate(self):
            pass

    async def scenario():
        sup = await main.detect_supported(Sess())
        assert "charge-mode" in sup and "pressure" not in sup
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
        assert sup == set(main.OPTIONAL_ENDPOINTS)   # read sensors default on, buttons hidden
        assert sess.invalidated is True

    asyncio.run(scenario())


def test_detect_supported_tolerates_a_probe_error():
    class V:
        def supports_endpoint(self, ep):
            if ep == "pressure":
                raise RuntimeError("probe blew up")
            return True

    class Sess:
        async def vehicle(self):
            return V()

        async def invalidate(self):
            pass

    async def scenario():
        sup = await main.detect_supported(Sess())
        # pressure stays in the default set (its probe failed, not a clean "unsupported")
        assert "pressure" in sup and "charge-mode" in sup

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# command dispatch
# --------------------------------------------------------------------------- #
def test_hvac_start_falls_back_to_21_on_schedule_error():
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


def test_run_command_unknown_is_noop():
    asyncio.run(main.run_command("does-not-exist"))   # warns, never raises


def test_run_command_debounces_repeat(monkeypatch):
    main._last_command.clear()
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)
    main._last_command["horn"] = 998.0               # 2s ago, inside the 5s window
    called = {"login": 0}

    async def login(ws, loc):
        called["login"] += 1

    monkeypatch.setattr(main, "_login_vehicle", login)
    asyncio.run(main.run_command("horn"))
    assert called["login"] == 0                       # suppressed, never logged in


class _CmdSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def test_run_command_success(monkeypatch):
    main._last_command.clear()
    monkeypatch.setattr(main, "now_ts", lambda: 2000.0)
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: _CmdSession())

    class V:
        def __init__(self):
            self.horn = False

        async def start_horn(self):
            self.horn = True

    v = V()

    async def login(ws, loc):
        return v

    monkeypatch.setattr(main, "_login_vehicle", login)
    asyncio.run(main.run_command("horn"))
    assert v.horn is True


def test_run_command_failure_is_logged_not_raised(monkeypatch):
    main._last_command.clear()
    monkeypatch.setattr(main, "now_ts", lambda: 3000.0)
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: _CmdSession())

    async def login(ws, loc):
        raise RuntimeError("auth failed")

    monkeypatch.setattr(main, "_login_vehicle", login)
    asyncio.run(main.run_command("horn"))             # logs error, never raises


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


def test_set_soc_ignores_non_numeric(monkeypatch):
    v = _SocVehicle()
    _soc_login(monkeypatch, v)
    asyncio.run(main.run_command("soc_max_target", "not-a-number"))
    assert v.soc_set is None             # never written


def test_set_soc_bails_when_opposing_limit_missing(monkeypatch):
    class _NoLimits(_SocVehicle):
        async def get_battery_soc(self):
            return _obj(socTarget=None, socMin=None)

    v = _NoLimits()
    _soc_login(monkeypatch, v)
    asyncio.run(main.run_command("soc_max_target", "90"))
    assert v.soc_set is None             # bailed: current limits unavailable


def test_set_soc_error_is_swallowed(monkeypatch):
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: _CmdSession())

    async def login(ws, loc):
        raise RuntimeError("auth failed")

    monkeypatch.setattr(main, "_login_vehicle", login)
    asyncio.run(main.run_command("soc_max_target", "90"))   # no raise


def test_concurrent_soc_sets_do_not_clobber(monkeypatch):
    car = {"min": 20, "target": 80}

    class V:
        async def get_battery_soc(self):
            return _obj(socMin=car["min"], socTarget=car["target"])

        async def set_battery_soc(self, *, min, target):
            await asyncio.sleep(0)               # yield — interleaves without the lock
            car["min"], car["target"] = min, target

    _soc_login(monkeypatch, V())

    async def both():
        await asyncio.gather(main.run_command("soc_min_target", "30"),
                             main.run_command("soc_max_target", "90"))

    asyncio.run(both())
    assert car == {"min": 30, "target": 90}      # both survived -> writes serialised


def test_numbers_not_debounced(monkeypatch):
    # A number set must not be dropped by the button debounce window.
    main._last_command.clear()
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)
    v = _SocVehicle()
    _soc_login(monkeypatch, v)
    asyncio.run(main.run_command("soc_max_target", "70"))
    asyncio.run(main.run_command("soc_max_target", "75"))   # immediate repeat still applies
    assert v.soc_set == (20, 75)


# --------------------------------------------------------------------------- #
# debug API dump
# --------------------------------------------------------------------------- #
def test_dump_api_redacts_secrets_and_never_raises(monkeypatch, caplog):
    monkeypatch.setenv("R5_VIN", "VF1SECRET")
    monkeypatch.delenv("R5_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("R5_USERNAME", "driver@example.com")

    class V:
        async def get_details(self):
            return _obj(raw_data={"vin": "VF1SECRET", "model": "R5 E-Tech"})

        async def get_battery_status(self):
            return {"batteryLevel": 80, "vin": "VF1SECRET"}

        async def get_cockpit(self):
            return [_obj(raw_data={"totalMileage": 100})]

        async def get_hvac_status(self):
            raise RuntimeError("hvac boom")

    with caplog.at_level(logging.WARNING, logger="renault_5"):
        asyncio.run(main.dump_api(V()))
    assert "API DEBUG DUMP" in caplog.text
    assert "VF1SECRET" not in caplog.text             # secret scrubbed everywhere
    assert "R5 E-Tech" in caplog.text                 # telemetry kept
    assert "hvac boom" in caplog.text                 # endpoint error captured, not fatal


def test_dump_api_probes_ranged_and_alerts(monkeypatch, caplog):
    monkeypatch.setenv("R5_VIN", "VF1SECRET")
    captured = {}

    class V:
        async def get_charges(self, start, end):
            captured["window"] = (start, end)
            return [_obj(raw_data={"chargeEnergyRecovered": 12})]

        async def get_charge_history(self, start, end, period):
            raise RuntimeError("forbidden")           # exercises the error branch

        async def get_full_endpoint(self, key):
            captured["alerts_key"] = key
            return "/resolved/alerts"

        async def http_get(self, path):
            captured["alerts_path"] = path
            return {"alerts": ["x"]}

    with caplog.at_level(logging.WARNING, logger="renault_5"):
        asyncio.run(main.dump_api(V()))
    start, end = captured["window"]
    assert (end - start).days == main._DEBUG_RANGE_DAYS   # charges window ~30 days
    assert captured["alerts_key"] == "alerts"             # alerts path resolved by key
    assert captured["alerts_path"] == "/resolved/alerts"  # then raw-GET
    assert "chargeEnergyRecovered" in caplog.text         # charges payload captured


def test_maybe_dump_api_runs_once_per_restart(monkeypatch):
    main._DEBUG_STATE["dumped"] = False
    monkeypatch.setenv("R5_DEBUG_DUMP", "true")
    calls = {"n": 0}

    async def fake_dump(v):
        calls["n"] += 1

    monkeypatch.setattr(main, "dump_api", fake_dump)

    async def scenario():
        await main.maybe_dump_api("veh")
        await main.maybe_dump_api("veh")

    asyncio.run(scenario())
    assert calls["n"] == 1
    main._DEBUG_STATE["dumped"] = False


def test_maybe_dump_api_skips_when_disabled(monkeypatch):
    main._DEBUG_STATE["dumped"] = False
    monkeypatch.setenv("R5_DEBUG_DUMP", "false")
    calls = {"n": 0}

    async def fake_dump(v):
        calls["n"] += 1

    monkeypatch.setattr(main, "dump_api", fake_dump)
    asyncio.run(main.maybe_dump_api("veh"))
    assert calls["n"] == 0


# --------------------------------------------------------------------------- #
# account resolution
# --------------------------------------------------------------------------- #
def test_resolve_account_uses_configured_id(monkeypatch):
    monkeypatch.setenv("R5_ACCOUNT_ID", "ACC-CONFIGURED")
    assert asyncio.run(main.resolve_account(object())) == "ACC-CONFIGURED"


def test_resolve_account_autodiscovers_myrenault(monkeypatch):
    monkeypatch.delenv("R5_ACCOUNT_ID", raising=False)

    class Client:
        async def get_person(self):
            return _obj(accounts=[_obj(accountType="OTHER", accountId="x"),
                                  _obj(accountType="MYRENAULT", accountId="ACC-9")])

    assert asyncio.run(main.resolve_account(Client())) == "ACC-9"


def test_resolve_account_raises_when_no_myrenault(monkeypatch):
    monkeypatch.delenv("R5_ACCOUNT_ID", raising=False)

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


class _ChargingBattery:
    batteryLevel = 55
    batteryAutonomy = 150
    batteryTemperature = 15
    chargingInstantaneousPower = 7.4
    chargingRemainingTime = 90
    batteryAvailableEnergy = 28.0
    plugStatus = 1
    timestamp = None                      # forces the iso(now_ts()) fallback

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
        return {"preconditioningTemperature": 20}

    async def get_battery_soc(self):
        return _obj(socTarget=90, socMin=10)

    async def get_location(self):
        return _obj(gpsLatitude=None, gpsLongitude=None, lastUpdateTime="t")


def test_poll_once_charging_and_no_gps_fix():
    data, loc = asyncio.run(main.poll_once(_Sess(_ChargingVehicle()), {}, 52.0, set(), "km"))
    assert data["charging"] == "on"
    assert data["charger_status"] == "Charging"
    assert data["charging_flap_status"] == "Open: Plugged In"
    assert data["battery_last_activity"] is not None      # iso(now_ts()) fallback
    assert loc is None                                    # lat/lon both None => no tracker attrs


def test_poll_once_rounds_gps_for_privacy():
    class _GpsVehicle(_ChargingVehicle):
        async def get_location(self):
            return _obj(gpsLatitude=51.512345, gpsLongitude=-0.123456, lastUpdateTime="t")

    _data, loc = asyncio.run(main.poll_once(_Sess(_GpsVehicle()), {}, 52.0, set(), "km"))
    assert loc["latitude"] == 51.5123 and loc["longitude"] == -0.1235   # rounded to 4 dp
    assert loc["gps_accuracy"] == 11                                    # ~11 m at 4 dp


class _MinBattery:
    batteryLevel = 70
    batteryAutonomy = 180
    batteryTemperature = None
    chargingInstantaneousPower = 0.0
    chargingRemainingTime = None
    batteryAvailableEnergy = None
    plugStatus = 0
    timestamp = "2026-01-01T00:00:00Z"

    def get_plug_status(self):
        return PlugState.UNPLUGGED

    def get_charging_status(self):
        return ChargeState.NOT_IN_CHARGE


class _AllSecondaryFail:
    async def get_battery_status(self):
        return _MinBattery()

    async def get_cockpit(self):
        raise RuntimeError("cockpit down")

    async def get_hvac_status(self):
        raise RuntimeError("hvac down")

    async def get_charge_schedule(self):
        raise RuntimeError("sched down")

    async def get_battery_soc(self):
        raise RuntimeError("soc down")

    async def get_tyre_pressure(self):
        raise RuntimeError("tyre down")

    async def get_charge_mode(self):
        raise RuntimeError("mode down")

    async def get_location(self):
        raise RuntimeError("loc down")


def test_poll_once_survives_every_secondary_endpoint_failing():
    data, loc = asyncio.run(
        main.poll_once(_Sess(_AllSecondaryFail()), {}, 52.0, {"pressure", "charge-mode"}, "km"))
    # battery-derived keys still present; every degraded endpoint simply omitted
    assert data["battery_level"] == 70
    assert data["charger_plug_status"] == "Disconnected"
    assert "vehicle_mileage" not in data and "cabin_temperature" not in data
    assert "soc_max_target" not in data and "tyre_pressure_fl" not in data
    assert "charge_mode" not in data and "gps_last_activity" not in data
    assert loc is None


# --------------------------------------------------------------------------- #
# main() — one full poll cycle (success + failure branch)
# --------------------------------------------------------------------------- #
def test_health_server_serves_200():
    import aiohttp

    async def scenario():
        runner = await main.start_health_server()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{main.HEALTH_PORT}/healthz") as r:
                    assert r.status == 200 and (await r.text()) == "ok"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_status_panel_routes(monkeypatch):
    import aiohttp

    async def scenario():
        main._LATEST.update(ok=True, version="testver", supported=["x", "y"],
                            data={"battery_level": 42, "charger_plug_status": "Plugged"})
        runner = await main.start_health_server()
        base = f"http://127.0.0.1:{main.HEALTH_PORT}"
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


def _wire_main(monkeypatch, tmp_path, poll):
    for k, v in {"R5_USERNAME": "u", "R5_PASSWORD": "p", "R5_VIN": "VF1",
                 "MQTT_HOST": "broker", "R5_LOCALE": "en_GB"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    fc = _FakeClient()
    monkeypatch.setattr(main, "mqtt_connect", lambda: fc)

    async def fake_detect(vs):
        return set(main.OPTIONAL_ENDPOINTS)

    monkeypatch.setattr(main, "detect_supported", fake_detect)

    async def fake_deploy():
        return None

    monkeypatch.setattr(main.deploy, "run_deploy", fake_deploy)

    async def fake_health():                       # don't bind a real port in the loop test
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


def test_main_runs_one_successful_cycle(monkeypatch, tmp_path):
    async def poll(stop, *a, **k):
        stop.set()                                # end the loop after this iteration
        return ({"battery_level": 80, "charger_plug_status": "Connected",
                 "charging": "on", "plug_suspect": "off"},
                {"latitude": 51.5, "longitude": -0.1})

    fc = _wire_main(monkeypatch, tmp_path, poll)
    asyncio.run(main.main())
    topics = [t for t, _ in fc.pubs]
    assert main.STATE_TOPIC in topics
    assert main.ATTR_TOPIC in topics              # location attrs published
    assert main.TRACKER_STATE_TOPIC in topics
    assert (main.AVAIL_TOPIC, "offline") in fc.pubs   # clean shutdown
    assert fc.stopped is True and fc.disconnected is True


def test_main_failure_branch_flags_auth_and_staleness(monkeypatch, tmp_path):
    async def poll(stop, *a, **k):
        stop.set()
        raise RuntimeError("HTTP 401 invalid credentials")

    fc = _wire_main(monkeypatch, tmp_path, poll)
    asyncio.run(main.main())
    state_payloads = [json.loads(p) for t, p in fc.pubs if t == main.STATE_TOPIC]
    assert any(d.get("api_auth_failure") == "on" for d in state_payloads)
    assert any(d.get("data_stale") == "on" for d in state_payloads)


def test_main_exits_without_required_config(monkeypatch):
    for k in ("R5_USERNAME", "R5_PASSWORD", "R5_VIN", "MQTT_HOST"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        asyncio.run(main.main())


# --------------------------------------------------------------------------- #
# deploy.py — local dashboard read + WebSocket deploy
# --------------------------------------------------------------------------- #


class _FakeWS:
    """Scripts the HA WebSocket: auth handshake then result frames keyed on the
    last command type sent."""

    def __init__(self, dashboards=(), resources=()):
        self._last = None
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


def test_run_deploy_skips_reserved_url_path(monkeypatch):
    # Reaches validation (token present) and returns before any WS connection / save.
    _deploy_env(monkeypatch, R5_DASHBOARD_URL_PATH="energy")
    asyncio.run(deploy.run_deploy())


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


def test_run_deploy_noop_when_disabled(monkeypatch):
    _deploy_env(monkeypatch, R5_DEPLOY_DASHBOARD="none")

    class Explode:
        def __init__(self, *a, **k):
            raise AssertionError("ClientSession must not be constructed when disabled")

    monkeypatch.setattr(deploy.aiohttp, "ClientSession", Explode)
    asyncio.run(deploy.run_deploy())                 # returns immediately


def test_run_deploy_skips_unknown_style(monkeypatch):
    _deploy_env(monkeypatch, R5_DEPLOY_DASHBOARD="fancy")

    class Explode:
        def __init__(self, *a, **k):
            raise AssertionError("must not connect for an unknown style")

    monkeypatch.setattr(deploy.aiohttp, "ClientSession", Explode)
    asyncio.run(deploy.run_deploy())


def test_run_deploy_skips_without_supervisor_token(monkeypatch):
    _deploy_env(monkeypatch, SUPERVISOR_TOKEN=None)

    class Explode:
        def __init__(self, *a, **k):
            raise AssertionError("must not connect without a token")

    monkeypatch.setattr(deploy.aiohttp, "ClientSession", Explode)
    asyncio.run(deploy.run_deploy())


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


def test_charger_card_none_when_no_entities_set(monkeypatch):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    assert deploy._charger_card() is None


def test_charger_card_skips_blank_and_null(monkeypatch):
    monkeypatch.setenv("R5_CHARGER_SMART_CHARGE", "switch.octopus_intelligent_smart_charge")
    monkeypatch.setenv("R5_CHARGER_BUMP_CHARGE", "")        # blank -> skipped
    monkeypatch.setenv("R5_CHARGER_TARGET_SOC", "null")     # bashio empty -> skipped
    monkeypatch.setenv("R5_CHARGER_TARGET_TIME", "select.octopus_intelligent_target_time")
    card = deploy._charger_card()
    assert card["type"] == "entities" and card["title"] == "Smart Charging"
    assert [r["name"] for r in card["entities"]] == ["Smart Charge", "Target Time"]


def test_fetch_dashboard_appends_charger_card_when_configured(monkeypatch, tmp_path):
    (tmp_path / "front-end.txt").write_text("- title: Home\n  cards: []\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    monkeypatch.setenv("R5_CHARGER_SMART_CHARGE", "switch.x")
    cfg = asyncio.run(deploy._fetch_dashboard("standard"))
    last = cfg["views"][0]["cards"][-1]
    assert last["type"] == "entities" and last["title"] == "Smart Charging"


def test_fetch_dashboard_no_charger_card_when_unset(monkeypatch, tmp_path):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    (tmp_path / "front-end.txt").write_text("- title: Home\n  cards: []\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    cfg = asyncio.run(deploy._fetch_dashboard("standard"))
    assert cfg["views"][0]["cards"] == []


def test_add_card_sections_layout():
    # the standard dashboard is a `sections` view — the card must land in a new grid section
    view = {"type": "sections", "sections": [{"type": "grid", "cards": []}]}
    deploy._add_card(view, {"type": "entities", "title": "Smart Charging"})
    assert view["sections"][-1] == {"type": "grid", "cards": [{"type": "entities", "title": "Smart Charging"}]}


def test_add_card_cards_layout():
    # the bubble dashboard is a plain `cards` view — the card is appended to cards
    view = {"cards": [{"type": "x"}]}
    deploy._add_card(view, {"type": "entities", "title": "Smart Charging"})
    assert view["cards"][-1]["title"] == "Smart Charging"


def test_ws_cmd_raises_on_unsuccessful_result():
    class WS:
        async def send_json(self, payload):
            self._last = payload

        async def receive_json(self):
            return {"id": self._last["id"], "type": "result",
                    "success": False, "error": {"message": "denied"}}

    api = deploy._WS(None, WS(), "tok")
    with pytest.raises(RuntimeError):
        asyncio.run(api.dashboards())


def test_detect_supported_tolerates_action_probe_error():
    class V:
        def supports_endpoint(self, ep):
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
