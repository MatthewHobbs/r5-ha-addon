"""Contract test against the REAL renault-api response models.

The poller reads specific attribute names + helper methods off renault-api's models
(`batteryLevel`, `batteryAutonomy`, `totalMileage`, `gpsLatitude`, `get_plug_status()`, …).
Those are pinned to `renault-api==0.5.12`, but a deliberate bump could rename a field or
change a helper and silently break the matching sensor — the rest of the suite uses synthetic
stubs that wouldn't notice. This loads a representative API response through the **library's
own schemas** and asserts every field/method the poller depends on, so such a change fails CI
instead of shipping a dead sensor. If this breaks after a renault-api bump, update the poller
(and this contract) together. See CLAUDE.md "Do not bump renault-api casually".
"""
import renault_api.kamereon.schemas as schemas
from renault_api.kamereon.enums import ChargeState, PlugState

# A representative Kamereon battery-status / cockpit / location payload (the shape the API
# returns for an R5), used only to exercise the real library schemas.
_BATTERY = {
    "timestamp": "2026-06-28T10:00:00Z", "batteryLevel": 80, "batteryTemperature": 20,
    "batteryAutonomy": 250, "batteryCapacity": 0, "batteryAvailableEnergy": 42,
    "plugStatus": 1, "chargingStatus": 1.0, "chargingRemainingTime": 30,
    "chargingInstantaneousPower": 7.4, "chargingRemainingTimeLastUpdateDateTime": None,
    "V2L_SystemStatusDisplay": 0,
}
_COCKPIT = {"totalMileage": 12345.6, "fuelAutonomy": None, "fuelQuantity": None}
_LOCATION = {"lastUpdateTime": "2026-06-28T09:00:00Z", "gpsLatitude": 51.5, "gpsLongitude": -0.1}
# get_charges returns an untyped model (raw_data only); the poller reads the per-session dicts
# straight off raw_data["charges"]. This pins both that the schema still surfaces raw_data and
# that _parse_charge_session reads the camelCase keys the Kamereon API actually returns.
_CHARGES = {"charges": [{
    "chargeStartDate": "2026-06-21T00:00:00+00:00", "chargeEndDate": "2026-06-21T03:00:00+00:00",
    "chargeStartBatteryLevel": 35, "chargeEndBatteryLevel": 80,
    "chargeBatteryLevelRecovered": 45, "chargeEnergyRecovered": 23.4,
    "chargeStartInstantaneousPower": 7.4,
}]}


def test_battery_status_model_contract():
    b = schemas.KamereonVehicleBatteryStatusDataSchema.load(_BATTERY)
    # every field the poller reads off the battery model:
    assert b.batteryLevel == 80
    assert b.batteryAutonomy == 250
    assert b.batteryTemperature == 20
    assert b.batteryAvailableEnergy == 42
    assert b.chargingInstantaneousPower == 7.4
    assert b.chargingRemainingTime == 30
    assert b.timestamp == "2026-06-28T10:00:00Z"
    # the decoded enums the poller keys plug/charge state on:
    assert b.get_plug_status() == PlugState.PLUGGED
    assert b.get_charging_status() == ChargeState.CHARGE_IN_PROGRESS


def test_cockpit_model_contract():
    c = schemas.KamereonVehicleCockpitDataSchema.load(_COCKPIT)
    assert c.totalMileage == 12345.6   # -> vehicle mileage sensor


def test_location_model_contract():
    loc = schemas.KamereonVehicleLocationDataSchema.load(_LOCATION)
    assert loc.gpsLatitude == 51.5 and loc.gpsLongitude == -0.1
    assert loc.lastUpdateTime == "2026-06-28T09:00:00Z"


def test_charges_model_contract():
    from renault_mqtt import charge
    charges = schemas.KamereonVehicleChargesDataSchema.load(_CHARGES)
    # renault-api exposes the charges list only via raw_data (the model itself is untyped)
    assert charges.raw_data["charges"][0]["chargeEndBatteryLevel"] == 80
    # and the poller turns that raw session into populated Last Charge fields
    lc = charge._parse_charge_session(charges.raw_data["charges"], 52.0)
    assert lc["last_charge_end_soc"] == 80
    assert lc["last_charge_recovered_pct"] == 45
    assert lc["last_charge_duration_min"] == 180   # 3 h from start/end timestamps


def test_soc_levels_model_contract():
    """The charge-limit chain, end to end: API field -> data key -> published entity_id.

    Mirrors the a290 twin's test, which was added after DOCS.md there documented
    `number.alpine_a290_soc_min_target` / `_soc_max_target` — THIS repo's ids, mirrored across
    without porting them. The lockstep rule cuts both ways, and nothing caught it.

    The payload below is the REAL captured response from renault-api's own fixture
    (`tests/fixtures/kamereon/vehicle_kcm_data/ev-soc-levels.json`), loaded through the
    library's own schema. `soc-levels` is declared for R5E1VE in `_VEHICLE_ENDPOINTS`.
    """
    import re

    import catalog

    soc = schemas.KamereonVehicleBatterySocDataSchema.load(
        {"lastEnergyUpdateTimestamp": "2025-04-18T06:51:09Z", "socMin": 20, "socTarget": 80})
    assert soc.socMin == 20
    assert soc.socTarget == 80

    # Data keys must equal object_id minus the prefix, or the MQTT value_template resolves to
    # nothing and the slider renders unavailable.
    keys = {o[len(catalog.OBJ_PREFIX):] for o in catalog.NUMBERS}
    assert keys == {"soc_min_target", "soc_max_target"}, keys

    # HA ignores the discovery object_id and derives slug(device name + friendly name). These
    # ids differ from the a290's on purpose — this repo keeps the original forked view's entity
    # names for backward compatibility, which is exactly why a mirrored doc passage is wrong.
    def slug(text):
        return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")

    device = catalog.DEVICE["name"]
    ids = {slug(f"{device} {meta[0]}") for meta in catalog.NUMBERS.values()}
    assert ids == {"r5_soc_min_target", "r5_soc_max_target"}, ids

    # The real captured values must sit inside the ranges the sliders advertise.
    ranges = {o[len(catalog.OBJ_PREFIX):]: (m[2], m[3]) for o, m in catalog.NUMBERS.items()}
    assert ranges["soc_min_target"][0] <= soc.socMin <= ranges["soc_min_target"][1]
    assert ranges["soc_max_target"][0] <= soc.socTarget <= ranges["soc_max_target"][1]
