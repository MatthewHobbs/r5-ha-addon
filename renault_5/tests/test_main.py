"""Unit tests for the Renault 5 poller — the pure helpers that stay in main after the seam
split (unit conversion, enum decoding, schedule summaries, plug-suspect detection), the
control-layer command/number maps, the cached login session, and the poll_once integration
contract. The config/util/debug/charge/mqtt seams are covered in their own test modules.
"""
import catalog
import charge
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


# --------------------------------------------------------------------------- #
# unit conversion / coercion helpers
# --------------------------------------------------------------------------- #
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
# KCM charge-schedule summary (from the ev/settings payload we already fetch)
# --------------------------------------------------------------------------- #
def test_charge_schedule_fields_extracts_kcm_settings():
    settings = {"preconditioningTemperature": 21, "chargeModeRq": "scheduled_charge",
                "chargeTimeStart": "0420", "chargeDuration": 480}
    out = main._charge_schedule_fields(settings)
    assert out["charge_schedule_mode"] == "Scheduled Charge"   # underscores -> title case
    assert out["scheduled_charge_start"] == "04:20"            # bare HHMM -> HH:MM
    assert out["scheduled_charge_duration"] == 480
    expected = {obj[len("r5_"):] for obj in catalog.SENSORS
                if obj.endswith(("charge_schedule_mode", "scheduled_charge_start",
                                 "scheduled_charge_duration"))}
    assert set(out) == expected


def test_charge_schedule_fields_absent_is_none():
    out = main._charge_schedule_fields({"preconditioningTemperature": 21})
    assert out == {"charge_schedule_mode": None, "scheduled_charge_start": None,
                   "scheduled_charge_duration": None}


def test_fmt_hhmm_only_reformats_bare_four_digits():
    assert main._fmt_hhmm("0700") == "07:00"
    assert main._fmt_hhmm("T07:00Z") == "T07:00Z"   # already formatted -> untouched
    assert main._fmt_hhmm(None) is None
    assert main._fmt_hhmm("") is None


# --------------------------------------------------------------------------- #
# HVAC preconditioning schedule (get_hvac_settings)
# --------------------------------------------------------------------------- #
class _Day:
    def __init__(self, ready):
        self.readyAtTime = ready


class _Sched:
    def __init__(self, activated, **days):
        self.activated = activated
        for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
            setattr(self, d, days.get(d))


class _HvacSettings:
    def __init__(self, mode, schedules):
        self.mode, self.schedules = mode, schedules


def test_hvac_schedule_fields_active_schedule():
    settings = _HvacSettings("scheduled_value", [
        _Sched(False, monday=_Day("T06:00Z")),                       # inactive -> ignored
        _Sched(True, monday=_Day("T07:00Z"), friday=_Day("0830")),   # active
    ])
    out = main._hvac_schedule_fields(settings)
    assert out["climate_schedule_mode"] == "Scheduled Value"
    assert out["climate_ready_time"] == "Mon 07:00, Fri 08:30"       # only the active schedule
    expected = {obj[len("r5_"):] for obj in catalog.SENSORS if obj.startswith("r5_climate_")}
    assert set(out) == expected


def test_hvac_schedule_fields_none_and_no_active():
    assert main._hvac_schedule_fields(None) == {"climate_schedule_mode": None,
                                                "climate_ready_time": None}
    out = main._hvac_schedule_fields(_HvacSettings("none", [_Sched(False, monday=_Day("T06:00Z"))]))
    assert out["climate_schedule_mode"] == "None" and out["climate_ready_time"] is None


def test_fmt_ready_normalises_time_forms():
    assert main._fmt_ready("T07:00Z") == "07:00"
    assert main._fmt_ready("08:30:00") == "08:30"
    assert main._fmt_ready("0915") == "09:15"
    assert main._fmt_ready(None) is None


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
# discovery contract — the class of bug that shipped broken Last Charge tiles
# --------------------------------------------------------------------------- #
def test_last_charge_data_keys_match_sensor_object_ids(monkeypatch):
    """Every Last Charge sensor's value_template key must be produced by the data dict.
    update_charge_session lives in the charge seam now, so patch charge.now_ts."""
    clock = {"t": 0.0}
    monkeypatch.setattr(charge, "now_ts", lambda: clock["t"])
    state = {}
    charge.update_charge_session(state, Battery(40, 7.0, 20.0), 52.0, charging=True)
    clock["t"] = 1800
    lc = charge.update_charge_session(state, Battery(80, 0.0, 40.0), 52.0, charging=False)
    produced = set(lc)
    expected = {obj.removeprefix("r5_") for obj in catalog.SENSORS if "last_charge" in obj}
    assert produced == expected
    assert not any(k.startswith("r5_") for k in produced)


# --------------------------------------------------------------------------- #
# control buttons (all five supported on the R5)
# --------------------------------------------------------------------------- #
def test_command_actions_cover_every_button():
    assert set(main.COMMAND_ACTIONS) == set(main.ACTION_BUTTONS)


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
# writable charge-limit numbers
# --------------------------------------------------------------------------- #
def test_number_cmds_and_roles():
    assert main.NUMBER_CMDS == {obj.removeprefix("r5_") for obj in main.NUMBERS}
    assert main._NUMBER_ROLE["soc_min_target"] == "min"
    assert main._NUMBER_ROLE["soc_max_target"] == "target"


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


# --------------------------------------------------------------------------- #
# dashboard car-render selection (deploy.py)
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# poll_once integration — the published-data contract
# --------------------------------------------------------------------------- #
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

    async def get_hvac_settings(self):
        self._maybe("hvac_settings")
        sched = _obj(activated=True, monday=_obj(readyAtTime="T07:00Z"), tuesday=None,
                     wednesday=None, thursday=None, friday=None, saturday=None, sunday=None)
        return _obj(mode="scheduled", schedules=[sched])

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
    core = {obj.removeprefix("r5_") for obj in catalog.SENSORS if "last_charge" not in obj}
    assert core - produced == set(), f"poll_once did not produce: {core - produced}"
    # the key the success-log line reads must exist (regression guard for the log-key bug)
    assert "charger_plug_status" in data
    assert data["charging"] == "off" and "plug_suspect" in data


def test_poll_once_degrades_when_one_endpoint_fails(monkeypatch):
    data, _loc = _poll(_FakeVehicle(fail={"hvac"}), {"charge-mode", "pressure"}, monkeypatch)
    assert "cabin_temperature" not in data and "hvac_status" not in data   # hvac skipped
    assert "battery_level" in data and "charger_plug_status" in data       # rest survive
