"""Unit tests for the Renault 5 poller.

Mirrors the A290 add-on's suite (the two share this data layer): the discovery-template/
data-key contract, charge-session maths, plug stuck-detection, enum decoding, unit
conversion, the action-button/command-map consistency, and the cached login session.
"""
import json

import main
import pytest
from renault_api.kamereon.enums import ChargeState, PlugState


class Battery:
    """Stand-in for renault-api's battery-status object (attr access + one method)."""

    def __init__(self, soc, power=0.0, energy=None, status=None):
        self.batteryLevel = soc
        self.chargingInstantaneousPower = power
        self.batteryAvailableEnergy = energy
        self._status = status

    def get_charging_status(self):
        return self._status


class StubClient:
    """Captures MQTT publishes so we can assert on discovery payloads."""

    def __init__(self):
        self.pub = {}

    def publish(self, topic, payload, retain=False):
        self.pub[topic] = payload


# --------------------------------------------------------------------------- #
# unit conversion / coercion helpers
# --------------------------------------------------------------------------- #
def test_num_rounds_and_tolerates_garbage():
    assert main._num("12.345") == 12.35
    assert main._num(None) is None
    assert main._num("not-a-number") is None


def test_dist_respects_locale_unit():
    assert main._dist(100, "km") == 100
    assert main._dist(100, "mi") == 62.1
    assert main._dist(None, "mi") is None


@pytest.mark.parametrize("truthy", [True, "true", "True", "on", "ON", 1, "1"])
def test_bool_on_truthy(truthy):
    assert main._bool_on(truthy) == "on"


@pytest.mark.parametrize("falsy", [False, "false", None, 0, "0", "off"])
def test_bool_on_falsy(falsy):
    assert main._bool_on(falsy) == "off"


# --------------------------------------------------------------------------- #
# enum decoding
# --------------------------------------------------------------------------- #
def test_enum_label_known_member():
    assert main._enum_label(ChargeState.CHARGE_IN_PROGRESS,
                            main.CHARGE_STATUS_LABELS, 1.0) == "Charging"


def test_enum_label_unmapped_member_is_prettified():
    assert main._enum_label(ChargeState.CHARGE_IN_PROGRESS, {}, 1.0) == "Charge In Progress"


def test_enum_label_none_uses_raw():
    assert main._enum_label(None, {}, None) == "Unknown"
    assert main._enum_label(None, {}, 0.2) == "Unknown (0.2)"


# --------------------------------------------------------------------------- #
# preconditioning payload search
# --------------------------------------------------------------------------- #
def test_find_precond_locates_nested_block():
    payload = {"data": {"attributes": {"ev": {"preconditioningTemperature": 21}}}}
    assert main._find_precond(payload) == {"preconditioningTemperature": 21}


def test_find_precond_returns_empty_when_absent_or_too_deep():
    assert main._find_precond({"foo": "bar"}) == {}
    assert main._find_precond("not a dict") == {}
    deep = {"data": {"data": {"data": {"data": {"data": {"preconditioningX": 1}}}}}}
    assert main._find_precond(deep) == {}


# --------------------------------------------------------------------------- #
# is_charging — status OR power-fallback
# --------------------------------------------------------------------------- #
def test_is_charging_by_status():
    assert main.is_charging(Battery(50, power=0.0, status=ChargeState.CHARGE_IN_PROGRESS)) is True


def test_is_charging_by_power_fallback():
    assert main.is_charging(Battery(50, power=6.0, status=ChargeState.NOT_IN_CHARGE)) is True


def test_not_charging():
    assert main.is_charging(Battery(50, power=0.0, status=ChargeState.NOT_IN_CHARGE)) is False


# --------------------------------------------------------------------------- #
# charge-session tracking
# --------------------------------------------------------------------------- #
def test_charge_session_lifecycle(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    main.update_charge_session(state, Battery(40, 7.0, 20.0), 52.0, charging=True)
    assert state["session_active"] is True
    main.update_charge_session(state, Battery(60, 7.0, 30.0), 52.0, charging=True)
    clock["t"] = 1000.0 + 1800
    lc = main.update_charge_session(state, Battery(80, 0.0, 40.0), 52.0, charging=False)
    assert state["session_active"] is False
    assert lc["last_charge_duration_min"] == 30
    assert lc["last_charge_recovered_pct"] == 40
    assert lc["last_charge_recovered_kwh"] == 20.0
    assert lc["last_charge_average_power"] == 7.0
    assert lc["last_charge_type"] == "Home"


def test_charge_session_energy_falls_back_to_soc_estimate(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 0.0)
    state = {}
    main.update_charge_session(state, Battery(50, 7.0, None), 52.0, charging=True)
    assert state["start_energy"] == pytest.approx(26.0)


def test_rapid_charge_is_classified_public(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    main.update_charge_session(state, Battery(20, 50.0, 10.0), 52.0, charging=True)
    clock["t"] = 1800
    lc = main.update_charge_session(state, Battery(60, 0.0, 31.0), 52.0, charging=False)
    assert lc["last_charge_type"] == "Rapid/Public"


# --------------------------------------------------------------------------- #
# authoritative Last Charge via the charges endpoint
# --------------------------------------------------------------------------- #
_CHARGE_ITEM = {
    "chargeStartDate": "2026-06-20T22:00:00+00:00",
    "chargeEndDate": "2026-06-21T02:00:00+00:00",   # 4 h later
    "chargeStartBatteryLevel": 30, "chargeEndBatteryLevel": 80,
    "chargeBatteryLevelRecovered": 50, "chargeEnergyRecovered": 26.0,
    "chargeStartInstantaneousPower": 7.0,
}


def test_parse_charge_session_picks_latest_and_computes():
    older = {**_CHARGE_ITEM, "chargeEndDate": "2026-06-10T02:00:00+00:00"}
    lc = main._parse_charge_session([older, _CHARGE_ITEM], 52.0)
    assert lc["last_charge_end"] == "2026-06-21T02:00:00+00:00"
    assert lc["last_charge_start_soc"] == 30 and lc["last_charge_end_soc"] == 80
    assert lc["last_charge_recovered_pct"] == 50
    assert lc["last_charge_recovered_kwh"] == 26.0
    assert lc["last_charge_duration_min"] == 240          # from timestamps, not chargeDuration
    assert lc["last_charge_average_power"] == 6.5         # 26 kWh / 4 h
    assert lc["last_charge_type"] == "Home"
    # produces exactly the Last Charge sensor keys (same contract as the inferred path)
    expected = {obj[len("r5_"):] for obj in main.SENSORS if "last_charge" in obj}
    assert set(lc) == expected


def test_parse_charge_session_empty_and_incomplete():
    assert main._parse_charge_session([], 52.0) == {}
    assert main._parse_charge_session(None, 52.0) == {}
    assert main._parse_charge_session([{"chargeStartDate": "2026-06-21T22:00:00+00:00"}], 52.0) == {}


def test_parse_charge_session_derives_missing_energy_from_soc():
    item = {"chargeStartDate": "2026-06-21T00:00:00+00:00",
            "chargeEndDate": "2026-06-21T01:00:00+00:00",
            "chargeStartBatteryLevel": 20, "chargeEndBatteryLevel": 40}
    lc = main._parse_charge_session([item], 50.0)
    assert lc["last_charge_recovered_pct"] == 20          # 40 - 20
    assert lc["last_charge_recovered_kwh"] == 10.0        # 20% of 50 kWh


def test_prefer_real_charge_matches_same_session_within_tolerance():
    real = {"last_charge_end": "2026-06-21T02:00:00+00:00"}
    assert main._prefer_real_charge(real, {}) is True        # nothing inferred yet -> use endpoint
    assert main._prefer_real_charge({}, real) is False       # no endpoint data -> keep inferred
    # endpoint's actual stop precedes the inferred (observed) stop by minutes -> same session,
    # authoritative record still wins (the bug codex caught: strict >= rejected this)
    live_observed_later = {"last_charge_end": "2026-06-21T02:05:00+00:00"}   # +5 min
    assert main._prefer_real_charge(real, live_observed_later) is True
    # a live session ending materially later (hours) is a fresh charge not yet posted -> keep it
    live_fresh = {"last_charge_end": "2026-06-21T06:00:00+00:00"}            # +4 h
    assert main._prefer_real_charge(real, live_fresh) is False
    assert main._prefer_real_charge({"last_charge_end": "garbage"}, live_fresh) is False


def test_due_for_charges_throttle(monkeypatch):
    monkeypatch.setattr(main, "now_ts", lambda: 10_000.0)
    assert main._due_for_charges({}) is True
    assert main._due_for_charges({"charges_last_fetch": 10_000.0}) is False
    assert main._due_for_charges({"charges_last_fetch": 0.0}) is True
    assert main._due_for_charges({"charges_last_fetch": 10_000.0, "charges_dirty": True}) is True


# --------------------------------------------------------------------------- #
# plug stuck-detection
# --------------------------------------------------------------------------- #
def test_plug_suspect_disconnected_but_charging():
    assert main.detect_plug_suspect({}, plug=PlugState.UNPLUGGED, mileage=1000, soc=50, charging=True) == "on"


def test_plug_suspect_connected_but_driven(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    assert main.detect_plug_suspect(state, plug=PlugState.PLUGGED, mileage=1000, soc=50, charging=False) == "off"
    clock["t"] = 1000.0 + 3600
    assert main.detect_plug_suspect(state, plug=PlugState.PLUGGED, mileage=1005, soc=46, charging=False) == "on"


def test_plug_suspect_quiet_when_genuinely_plugged(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    main.detect_plug_suspect(state, plug=PlugState.PLUGGED, mileage=1000, soc=50, charging=False)
    clock["t"] = 1000.0 + 3600
    assert main.detect_plug_suspect(state, plug=PlugState.PLUGGED, mileage=1000, soc=50, charging=False) == "off"


# --------------------------------------------------------------------------- #
# discovery contract
# --------------------------------------------------------------------------- #
def test_last_charge_data_keys_match_sensor_object_ids(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    main.update_charge_session(state, Battery(40, 7.0, 20.0), 52.0, charging=True)
    clock["t"] = 1800
    lc = main.update_charge_session(state, Battery(80, 0.0, 40.0), 52.0, charging=False)
    produced = set(lc)
    expected = {obj.removeprefix("r5_") for obj in main.SENSORS if "last_charge" in obj}
    assert produced == expected
    assert not any(k.startswith("r5_") for k in produced)


def test_sensor_value_templates_strip_the_prefix():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "km")
    for obj in main.SENSORS:
        payload = c.pub.get(f"homeassistant/sensor/{main.NODE}/{obj}/config")
        assert payload, f"{obj} not published"
        conf = json.loads(payload)
        assert conf["value_template"] == "{{ value_json.%s }}" % obj.removeprefix("r5_")


def test_binary_sensor_value_templates_strip_the_prefix():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "km")
    for obj in main.BINARY_SENSORS:
        payload = c.pub.get(f"homeassistant/binary_sensor/{main.NODE}/{obj}/config")
        assert payload, f"{obj} not published"
        conf = json.loads(payload)
        assert conf["value_template"] == "{{ value_json.%s }}" % obj.removeprefix("r5_")


def test_optional_sensors_cleared_when_unsupported():
    c = StubClient()
    main.publish_discovery(c, set(), "km")
    for obj in main.OPTIONAL_ENDPOINTS["pressure"]:
        assert c.pub[f"homeassistant/sensor/{main.NODE}/{obj}/config"] == ""


def test_distance_device_class_dropped_only_for_miles():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "km")
    for obj in ("r5_battery_autonomy", "r5_vehicle_mileage"):
        conf = json.loads(c.pub[f"homeassistant/sensor/{main.NODE}/{obj}/config"])
        assert conf.get("device_class") == "distance" and conf["unit_of_measurement"] == "km"

    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "mi")
    for obj in ("r5_battery_autonomy", "r5_vehicle_mileage"):
        conf = json.loads(c.pub[f"homeassistant/sensor/{main.NODE}/{obj}/config"])
        assert "device_class" not in conf and conf["unit_of_measurement"] == "mi"


# --------------------------------------------------------------------------- #
# control buttons (all five supported on the R5)
# --------------------------------------------------------------------------- #
def test_command_actions_cover_every_button():
    assert set(main.COMMAND_ACTIONS) == set(main.ACTION_BUTTONS)


def test_buttons_published_when_supported():
    c = StubClient()
    all_eps = {ep for _oid, _node, _name, _icon, ep in main.ACTION_BUTTONS.values()}
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS) | all_eps, "km")
    for _cmd, (oid, node, name, _icon, _ep) in main.ACTION_BUTTONS.items():
        conf = json.loads(c.pub[f"homeassistant/button/{main.NODE}/{node}/config"])
        assert conf["name"] == name
        assert conf["object_id"] == oid


def test_buttons_cleared_when_action_endpoint_unsupported():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "km")   # no action endpoints
    for _cmd, (_oid, node, _name, _icon, _ep) in main.ACTION_BUTTONS.items():
        assert c.pub[f"homeassistant/button/{main.NODE}/{node}/config"] == ""


# --------------------------------------------------------------------------- #
# writable charge-limit numbers
# --------------------------------------------------------------------------- #
def test_numbers_published_when_soc_supported():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS) | {main.SOC_ENDPOINT}, "mi")
    for obj, (name, _icon, _role, mn, mx, step) in main.NUMBERS.items():
        short = obj.removeprefix("r5_")
        conf = json.loads(c.pub[f"homeassistant/number/{main.NODE}/{short}/config"])
        assert conf["name"] == name
        assert conf["command_topic"] == f"{main.CMD_PREFIX}{short}"
        assert conf["value_template"] == "{{ value_json.%s }}" % short
        assert (conf["min"], conf["max"], conf["step"]) == (mn, mx, step)
        assert conf["device_class"] == "battery" and conf["unit_of_measurement"] == "%"


def test_numbers_cleared_when_soc_unsupported():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "mi")   # soc-levels not supported
    for obj in main.NUMBERS:
        assert c.pub[f"homeassistant/number/{main.NODE}/{obj.removeprefix('r5_')}/config"] == ""


def test_retired_soc_sensors_are_cleared():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS) | {main.SOC_ENDPOINT}, "mi")
    for obj in ("r5_soc_max_target", "r5_soc_min_target"):
        assert c.pub[f"homeassistant/sensor/{main.NODE}/{obj}/config"] == ""


def test_number_cmds_and_roles():
    assert main.NUMBER_CMDS == {obj.removeprefix("r5_") for obj in main.NUMBERS}
    assert main._NUMBER_ROLE["soc_min_target"] == "min"
    assert main._NUMBER_ROLE["soc_max_target"] == "target"


def test_command_actions_dispatch_to_real_methods():
    import asyncio

    class FakeVehicle:
        def __init__(self):
            self.calls = []

        async def set_charge_start(self):
            self.calls.append("set_charge_start")

        async def start_horn(self):
            self.calls.append("start_horn")

        async def start_lights(self):
            self.calls.append("start_lights")

        async def set_ac_start(self, temp):
            self.calls.append(("set_ac_start", temp))

        async def set_ac_stop(self):
            self.calls.append("set_ac_stop")

        async def refresh_location(self):
            self.calls.append("refresh_location")

        async def get_charge_schedule(self):
            return {"preconditioningTemperature": 19}

    async def scenario():
        for cmd, expect in [("charge_start", "set_charge_start"), ("horn", "start_horn"),
                            ("lights", "start_lights"), ("hvac_stop", "set_ac_stop"),
                            ("refresh_location", "refresh_location")]:
            v = FakeVehicle()
            await main.COMMAND_ACTIONS[cmd](v)
            assert expect in v.calls
        # hvac_start reads the car's preconditioning temp, then starts the AC
        v = FakeVehicle()
        await main.COMMAND_ACTIONS["hvac_start"](v)
        assert ("set_ac_start", 19.0) in v.calls

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# cached login session
# --------------------------------------------------------------------------- #
def test_vehicle_session_reuses_login_and_reauths_after_invalidate(monkeypatch):
    import asyncio

    calls = {"login": 0, "closed": 0}

    class FakeSession:
        async def close(self):
            calls["closed"] += 1

    async def fake_login(websession, locale):
        calls["login"] += 1
        return f"vehicle#{calls['login']}"

    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: FakeSession())
    monkeypatch.setattr(main, "_login_vehicle", fake_login)

    async def scenario():
        vs = main.VehicleSession("en_GB")
        first = await vs.vehicle()
        again = await vs.vehicle()
        assert first is again
        assert calls["login"] == 1
        await vs.invalidate()
        assert calls["closed"] == 1
        assert await vs.vehicle() == "vehicle#2"
        assert calls["login"] == 2
        await vs.close()
        assert calls["closed"] == 2

    asyncio.run(scenario())


# debug API dump --------------------------------------------------------------

def test_debug_redact_masks_ids_and_secrets_but_keeps_telemetry():
    payload = {
        "vin": "VF1SECRET",
        "registrationNumber": "PLATE123",
        "batteryLevel": 80,
        "gpsLatitude": 51.5,
        "owner": {"firstName": "Matt", "email": "x@y.z", "note": "car VF1SECRET here"},
        "programs": [{"tcuCode": "T1"}],
    }
    out = main._debug_redact(payload, ["VF1SECRET"])
    assert out["vin"] == "***"                       # id key masked
    assert out["registrationNumber"] == "***"        # plate masked
    assert out["batteryLevel"] == 80                 # telemetry kept
    assert out["gpsLatitude"] == "***"               # location masked (it's PII, not telemetry)
    assert out["owner"]["firstName"] == "***"        # contact field masked
    assert out["owner"]["note"] == "car *** here"    # secret value scrubbed in-string
    assert out["programs"][0]["tcuCode"] == "***"    # nested list+id masked


def test_debug_redact_masks_non_string_id_and_numeric_secret():
    # an identifier held as a number must still be masked (key-based, any type)
    out = main._debug_redact({"vin": 12345, "iccid": 999, "batteryLevel": 80}, [])
    assert out["vin"] == "***" and out["iccid"] == "***" and out["batteryLevel"] == 80
    # a configured secret value that comes back as a number is masked by value
    assert main._debug_redact({"acc": 7788}, ["7788"]) == {"acc": "***"}
    # list of dicts (the get_* list-returning shape) is recursed
    out2 = main._debug_redact([{"contractId": "C1"}, {"ok": 1}], [])
    assert out2[0]["contractId"] == "***" and out2[1]["ok"] == 1


def test_debug_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("R5_DEBUG_DUMP", raising=False)
    assert main.debug_enabled() is False
    monkeypatch.setenv("R5_DEBUG_DUMP", "true")
    assert main.debug_enabled() is True
    monkeypatch.setenv("R5_DEBUG_DUMP", "false")
    assert main.debug_enabled() is False


# dashboard car-render selection (deploy.py) ----------------------------------

def test_selected_render_resolves_trim_folder(monkeypatch):
    import deploy
    monkeypatch.setenv("R5_CAR_RENDER", "midnight-blue-iconic")
    assert deploy._selected_render() == "Images/Models/Iconic/midnight-blue-iconic.webp"
    monkeypatch.setenv("R5_CAR_RENDER", "matte-grey-roland-garros")
    assert deploy._selected_render() == "Images/Models/Roland%20Garros/matte-grey-roland-garros.webp"
    monkeypatch.setenv("R5_CAR_RENDER", "pop-yellow-techno-black-roof")
    assert deploy._selected_render() == "Images/Models/Techno/pop-yellow-techno-black-roof.webp"
    monkeypatch.delenv("R5_CAR_RENDER", raising=False)
    assert deploy._selected_render() is None


# poll_once integration — the published-data contract -------------------------

class _FakeBattery:
    batteryLevel = 80
    batteryAutonomy = 200
    batteryTemperature = 18
    chargingInstantaneousPower = 0.0
    chargingRemainingTime = None
    batteryAvailableEnergy = 40.0
    plugStatus = 0
    timestamp = "2026-01-01T00:00:00Z"

    def get_plug_status(self):
        return PlugState.UNPLUGGED

    def get_charging_status(self):
        return ChargeState.NOT_IN_CHARGE


def _obj(**attrs):
    return type("Obj", (), attrs)()


class _FakeVehicle:
    """Returns minimal objects for every endpoint poll_once touches; `fail` raises one."""

    def __init__(self, fail=()):
        self._fail = set(fail)

    def _maybe(self, name):
        if name in self._fail:
            raise RuntimeError(f"{name} boom")

    async def get_battery_status(self):
        self._maybe("battery")
        return _FakeBattery()

    async def get_cockpit(self):
        self._maybe("cockpit")
        return _obj(totalMileage=12345)

    async def get_hvac_status(self):
        self._maybe("hvac")
        return _obj(externalTemperature=9, internalTemperature=20, hvacStatus="off",
                    socThreshold=30, lastUpdateTime="t")

    async def get_charge_schedule(self):
        self._maybe("sched")
        return {"preconditioningTemperature": 21, "preconditioningHeatedStrgWheel": True,
                "preconditioningHeatedLeftSeat": False, "preconditioningHeatedRightSeat": True}

    async def get_battery_soc(self):
        self._maybe("soc")
        return _obj(socTarget=80, socMin=20)

    async def get_tyre_pressure(self):
        return _obj(flPressure=2.4, frPressure=2.4, rlPressure=2.4, rrPressure=2.4)

    async def get_charge_mode(self):
        return _obj(chargeMode="always")

    async def get_location(self):
        self._maybe("loc")
        return _obj(gpsLatitude=51.5, gpsLongitude=-0.1, lastUpdateTime="t")


class _FakeSession:
    locale = "en_GB"

    def __init__(self, vehicle):
        self._v = vehicle

    async def vehicle(self):
        return self._v


def _poll(vehicle, supported, monkeypatch):
    import asyncio
    monkeypatch.delenv("R5_DEBUG_DUMP", raising=False)
    monkeypatch.setattr(main, "now_ts", lambda: 1000.0)
    return asyncio.run(main.poll_once(_FakeSession(vehicle), {}, 52.0, supported, "mi"))


def test_poll_once_produces_every_core_sensor_key(monkeypatch):
    data, _loc = _poll(_FakeVehicle(), {"charge-mode", "pressure"}, monkeypatch)
    produced = set(data)
    # every published sensor key (minus r5_) except last_charge_* (needs a completed session)
    core = {obj.removeprefix("r5_") for obj in main.SENSORS if "last_charge" not in obj}
    assert core - produced == set(), f"poll_once did not produce: {core - produced}"
    # the key the success-log line reads must exist (regression guard for the log-key bug)
    assert "charger_plug_status" in data
    assert data["charging"] == "off" and "plug_suspect" in data


def test_poll_once_degrades_when_one_endpoint_fails(monkeypatch):
    data, _loc = _poll(_FakeVehicle(fail={"hvac"}), {"charge-mode", "pressure"}, monkeypatch)
    assert "cabin_temperature" not in data and "hvac_status" not in data   # hvac skipped
    assert "battery_level" in data and "charger_plug_status" in data       # rest survive
