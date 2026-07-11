"""Tests for the debug seam: the one-shot `debug_dump` diagnostic and its payload redaction
(`debug_enabled` / `_debug_redact` / `_dump_one` / `dump_api` / `maybe_dump_api`). The
redaction here is the structural key/value scrubber for decoded API payloads — its job is to
never leak identifiers/secrets into the WARNING dump."""
import asyncio
import logging

import debug


def _obj(**attrs):
    return type("Obj", (), attrs)()


# --------------------------------------------------------------------------- #
# payload redaction
# --------------------------------------------------------------------------- #
def test_debug_redact_masks_ids_and_secrets_but_keeps_telemetry():
    payload = {
        "vin": "VF1SECRET",
        "registrationNumber": "PLATE123",
        "batteryLevel": 80,
        "gpsLatitude": 51.5,
        "owner": {"firstName": "Matt", "email": "x@y.z", "note": "car VF1SECRET here"},
        "programs": [{"tcuCode": "T1"}],
    }
    out = debug._debug_redact(payload, ["VF1SECRET"])
    assert out["vin"] == "***"                       # id key masked
    assert out["registrationNumber"] == "***"        # plate masked
    assert out["batteryLevel"] == 80                 # telemetry kept
    assert out["gpsLatitude"] == "***"               # location masked (it's PII, not telemetry)
    assert out["owner"]["firstName"] == "***"        # contact field masked
    assert out["owner"]["note"] == "car *** here"    # secret value scrubbed in-string
    assert out["programs"][0]["tcuCode"] == "***"    # nested list+id masked


def test_debug_redact_masks_lifecycle_privacy_buildspec_and_token_keys():
    """Quasi-identifying lifecycle/privacy fields, the build-spec `assets` block, and
    token-ish field names are masked by key regardless of value type/shape."""
    out = debug._debug_redact(
        {
            "deliveryDate": "2024-03-01",
            "firstRegistrationDate": "2024-03-15",
            "vehicleId": 1234567,
            "privacyMode": "off",
            "privacyModeUpdateDate": "2024-04-01",
            "svtFlag": False,
            "svtBlockFlag": False,
            "batteryCode": "BC-XYZ",
            "assets": [{"renditions": [{"url": "https://3dv.renault.com/VCD/abc"}]}],
            "accessToken": "ey.real.token",
            "refreshToken": "ey.refresh",
            "gigyaCookieValue": "cookie",
            "batteryLevel": 80,             # telemetry — must survive
        },
        [],
    )
    for key in ("deliveryDate", "firstRegistrationDate", "vehicleId", "privacyMode",
                "privacyModeUpdateDate", "svtFlag", "svtBlockFlag", "batteryCode",
                "assets", "accessToken", "refreshToken", "gigyaCookieValue"):
        assert out[key] == "***", key
    assert out["batteryLevel"] == 80


def test_debug_redact_masks_non_string_id_and_numeric_secret():
    # an identifier held as a number must still be masked (key-based, any type)
    out = debug._debug_redact({"vin": 12345, "iccid": 999, "batteryLevel": 80}, [])
    assert out["vin"] == "***" and out["iccid"] == "***" and out["batteryLevel"] == 80
    # a configured secret value that comes back as a number is masked by value
    assert debug._debug_redact({"acc": 7788}, ["7788"]) == {"acc": "***"}
    # list of dicts (the get_* list-returning shape) is recursed
    out2 = debug._debug_redact([{"contractId": "C1"}, {"ok": 1}], [])
    assert out2[0]["contractId"] == "***" and out2[1]["ok"] == 1


def test_debug_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("R5_DEBUG_DUMP", raising=False)
    assert debug.debug_enabled() is False
    monkeypatch.setenv("R5_DEBUG_DUMP", "true")
    assert debug.debug_enabled() is True
    monkeypatch.setenv("R5_DEBUG_DUMP", "false")
    assert debug.debug_enabled() is False


# --------------------------------------------------------------------------- #
# dump_api / maybe_dump_api
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
        asyncio.run(debug.dump_api(V()))
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
        asyncio.run(debug.dump_api(V()))
    start, end = captured["window"]
    assert (end - start).days == debug._DEBUG_RANGE_DAYS   # charges window ~30 days
    assert captured["alerts_key"] == "alerts"             # alerts path resolved by key
    assert captured["alerts_path"] == "/resolved/alerts"  # then raw-GET
    assert "chargeEnergyRecovered" in caplog.text         # charges payload captured


def test_maybe_dump_api_runs_once_per_restart(monkeypatch):
    debug._DEBUG_STATE["dumped"] = False
    monkeypatch.setenv("R5_DEBUG_DUMP", "true")
    calls = {"n": 0}

    async def fake_dump(v):
        calls["n"] += 1

    monkeypatch.setattr(debug, "dump_api", fake_dump)

    async def scenario():
        await debug.maybe_dump_api("veh")
        await debug.maybe_dump_api("veh")

    asyncio.run(scenario())
    assert calls["n"] == 1
    debug._DEBUG_STATE["dumped"] = False


def test_maybe_dump_api_skips_when_disabled(monkeypatch):
    debug._DEBUG_STATE["dumped"] = False
    monkeypatch.setenv("R5_DEBUG_DUMP", "false")
    calls = {"n": 0}

    async def fake_dump(v):
        calls["n"] += 1

    monkeypatch.setattr(debug, "dump_api", fake_dump)
    asyncio.run(debug.maybe_dump_api("veh"))
    assert calls["n"] == 0
