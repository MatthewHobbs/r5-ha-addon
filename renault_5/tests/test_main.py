"""Unit tests for the Renault 5 poller.

Mirrors the A290 add-on's suite (the two share this data layer): the discovery-template/
data-key contract, charge-session maths, plug stuck-detection, enum decoding, unit
conversion, the action-button/command-map consistency, and the cached login session.
"""
import json

import main
import pytest
from renault_api.kamereon.enums import ChargeState


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
# plug stuck-detection
# --------------------------------------------------------------------------- #
def test_plug_suspect_disconnected_but_charging():
    assert main.detect_plug_suspect({}, plug=0, mileage=1000, soc=50, charging=True) == "on"


def test_plug_suspect_connected_but_driven(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    assert main.detect_plug_suspect(state, plug=1, mileage=1000, soc=50, charging=False) == "off"
    clock["t"] = 1000.0 + 3600
    assert main.detect_plug_suspect(state, plug=1, mileage=1005, soc=46, charging=False) == "on"


def test_plug_suspect_quiet_when_genuinely_plugged(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(main, "now_ts", lambda: clock["t"])
    state = {}
    main.detect_plug_suspect(state, plug=1, mileage=1000, soc=50, charging=False)
    clock["t"] = 1000.0 + 3600
    assert main.detect_plug_suspect(state, plug=1, mileage=1000, soc=50, charging=False) == "off"


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


# --------------------------------------------------------------------------- #
# control buttons (all five supported on the R5)
# --------------------------------------------------------------------------- #
def test_command_actions_cover_every_button():
    assert set(main.COMMAND_ACTIONS) == set(main.ACTION_BUTTONS)


def test_buttons_published_when_supported():
    c = StubClient()
    all_eps = {ep for _oid, _node, _name, _icon, ep in main.ACTION_BUTTONS.values()}
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS) | all_eps, "km")
    for _cmd, (oid, node, name, icon, _ep) in main.ACTION_BUTTONS.items():
        conf = json.loads(c.pub[f"homeassistant/button/{main.NODE}/{node}/config"])
        assert conf["name"] == name
        assert conf["object_id"] == oid


def test_buttons_cleared_when_action_endpoint_unsupported():
    c = StubClient()
    main.publish_discovery(c, set(main.OPTIONAL_ENDPOINTS), "km")   # no action endpoints
    for _cmd, (_oid, node, _name, _icon, _ep) in main.ACTION_BUTTONS.items():
        assert c.pub[f"homeassistant/button/{main.NODE}/{node}/config"] == ""


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
    assert out["gpsLatitude"] == 51.5                # telemetry kept
    assert out["owner"]["firstName"] == "***"        # contact field masked
    assert out["owner"]["note"] == "car *** here"    # secret value scrubbed in-string
    assert out["programs"][0]["tcuCode"] == "***"    # nested list+id masked


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
    assert deploy._selected_render() == "Images/Models/Iconic/midnight-blue-iconic.png"
    monkeypatch.setenv("R5_CAR_RENDER", "matte-grey-roland-garros")
    assert deploy._selected_render() == "Images/Models/Roland%20Garros/matte-grey-roland-garros.png"
    monkeypatch.setenv("R5_CAR_RENDER", "pop-yellow-techno-black-roof")
    assert deploy._selected_render() == "Images/Models/Techno/pop-yellow-techno-black-roof.png"
    monkeypatch.delenv("R5_CAR_RENDER", raising=False)
    assert deploy._selected_render() is None
