"""Unit tests for the Renault 5 poller — the pure helpers that stay in main after the seam
split (unit conversion, enum decoding, schedule summaries, plug-suspect detection), the
control-layer command/number maps, the cached login session, and the poll_once integration
contract. The config/util/debug/charge/mqtt seams are covered in their own test modules.
"""
import catalog
import main
import pytest
from renault_api.kamereon.enums import ChargeState, PlugState
from renault_api.kamereon.exceptions import UnauthorizedException
from renault_mqtt import charge


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


# --------------------------------------------------------------------------- #
# plug stuck-detection
# --------------------------------------------------------------------------- #
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
    # ACTION_BUTTONS is keyed by object_id; the command suffix is the override or the short form.
    cmds = {catalog.BUTTON_CMD_OVERRIDES.get(oid, oid.removeprefix("r5_"))
            for oid in main.ACTION_BUTTONS}
    assert cmds == set(main.COMMAND_ACTIONS)


def test_command_actions_dispatch_to_real_methods(monkeypatch):
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
        # with no option set, hvac_start reads the car's preconditioning temp, then starts the AC
        v = FakeVehicle()
        await main.COMMAND_ACTIONS["hvac_start"](v)
        assert ("set_ac_start", 19.0) in v.calls

    monkeypatch.delenv("R5_PRECONDITION_TEMPERATURE", raising=False)
    asyncio.run(scenario())


def test_start_climate_sends_the_option_when_set(monkeypatch):
    import asyncio
    monkeypatch.setenv("R5_PRECONDITION_TEMPERATURE", "23")

    class V:
        sent = None

        async def get_charge_schedule(self):
            raise AssertionError("the car's schedule is not read when the option is set")

        async def set_ac_start(self, temp):
            type(self).sent = temp

    asyncio.run(main._hvac_start_action(V()))
    assert V.sent == 23.0


def test_precondition_temp_unset_means_the_car_decides(monkeypatch):
    monkeypatch.delenv("R5_PRECONDITION_TEMPERATURE", raising=False)
    assert main._precondition_temp() is None


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
@pytest.mark.parametrize("first_attempt", ["success", "refused", "timeout"])
def test_vehicle_session_wires_transport_login_and_reauthenticates(monkeypatch, caplog, first_attempt):
    """Exercise the shared cache through the real app login at the Renault boundary."""
    import asyncio

    calls = {"login": 0, "closed": 0, "sessions": []}
    settings = {"R5_USERNAME": "driver@example.invalid", "R5_PASSWORD": "test-only",
                "R5_ACCOUNT_ID": "test-account", "R5_VIN": "test-vin"}
    vehicles = [object(), object()]
    failures = int(first_attempt != "success")

    class FakeSession:
        def __init__(self, *, timeout):
            assert timeout is main.API_TIMEOUT
            calls["sessions"].append(self)

        async def close(self):
            calls["closed"] += 1

    class Client:
        def __init__(self, *, websession, locale):
            assert websession is calls["sessions"][-1]
            assert locale == "fr_FR"
            self.session = self

        async def login(self, username, password):
            assert (username, password) == (settings["R5_USERNAME"], settings["R5_PASSWORD"])
            calls["login"] += 1
            if calls["login"] == 1:
                if first_attempt == "refused":
                    raise RuntimeError("login refused")
                if first_attempt == "timeout":
                    await asyncio.Event().wait()

        async def get_api_account(self, account_id):
            assert account_id == settings["R5_ACCOUNT_ID"]
            return self

        async def get_api_vehicle(self, vin):
            assert vin == settings["R5_VIN"]
            return vehicles[calls["login"] - 1 - failures]

    monkeypatch.setattr(main.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(main, "RenaultClient", Client)
    monkeypatch.setattr(main, "cfg", lambda key: settings[key])
    caplog.set_level("INFO", logger=main.LOG.name)

    async def scenario():
        vs = main.VehicleSession("fr_FR")
        assert calls["sessions"] == []
        if failures:
            error = RuntimeError if first_attempt == "refused" else TimeoutError
            with pytest.raises(error):
                await asyncio.wait_for(vs.vehicle(), timeout=0.01)
            assert calls["closed"] == 1
            assert not any("session cached for reuse" in r.message for r in caplog.records)
        first = await vs.vehicle()
        again = await vs.vehicle()
        assert first is again is vehicles[0]
        assert calls["login"] == 1 + failures

        await vs.invalidate()            # drop the session
        assert calls["closed"] == 1 + failures
        assert await vs.vehicle() is vehicles[1]
        assert calls["login"] == 2 + failures

        await vs.close()
        assert calls["closed"] == 2 + failures

    asyncio.run(scenario())
    assert sum("session cached for reuse" in r.message for r in caplog.records) == 2


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
    data, _loc = _poll(_FakeVehicle(), {"charge-mode", "hvac-settings", "pressure"}, monkeypatch)
    produced = set(data)
    # every published sensor key (minus r5_) except last_charge_* (needs a completed session)
    # and last_successful_poll, which is OUR clock rather than the car's — the poll loop stamps
    # it after poll_once returns, so poll_once legitimately never produces it.
    core = {obj.removeprefix("r5_") for obj in catalog.SENSORS
            if "last_charge" not in obj and obj != "r5_last_successful_poll"}
    assert core - produced == set(), f"poll_once did not produce: {core - produced}"
    # the key the success-log line reads must exist (regression guard for the log-key bug)
    assert "charger_plug_status" in data
    assert data["charging"] == "off" and "plug_suspect" in data


class _UnauthorizedVehicle(_FakeVehicle):
    """The same fake, but the named requests answer with Kamereon's unauthorized reply."""

    def _maybe(self, name):
        if name in self._fail:
            raise UnauthorizedException("err.func.wired.unauthorized", "Not authorized")


def test_unauthorized_on_the_battery_request_escapes_poll_once(monkeypatch):
    # The battery request is the one poll_once does not guard, so Kamereon's unauthorized reply on
    # it reaches the main loop's classifier (test_runtime.py). This pins that half of the path.
    with pytest.raises(UnauthorizedException):
        _poll(_UnauthorizedVehicle(fail={"battery"}), set(), monkeypatch)


def test_unauthorized_on_an_optional_request_is_swallowed_by_poll_once(monkeypatch):
    # Every optional request catches its own errors, so the same reply is logged, the poll carries
    # on, and the sensor is not turned on: it never reaches the classifier.
    data, _loc = _poll(_UnauthorizedVehicle(fail={"cockpit"}), set(), monkeypatch)
    assert data["battery_level"] == 80
    assert "vehicle_mileage" not in data


def test_poll_once_degrades_when_one_endpoint_fails(monkeypatch):
    data, _loc = _poll(_FakeVehicle(fail={"hvac"}), {"charge-mode", "pressure"}, monkeypatch)
    assert "cabin_temperature" not in data and "hvac_status" not in data   # hvac skipped
    assert "battery_level" in data and "charger_plug_status" in data       # rest survive
