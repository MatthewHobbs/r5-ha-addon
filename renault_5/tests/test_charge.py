"""Tests for the charge seam: the two-source Last Charge reconciliation — the inferred
session tracker (update_charge_session) and the authoritative charges-endpoint path
(_parse_charge_session / _prefer_real_charge / _due_for_charges). Charge maths and the
endpoint-vs-inference precedence are the class of logic that has shipped broken Last Charge
tiles, so they're pinned here.

The moved functions resolve now_ts in the charge module's namespace, so tests patch
`charge.now_ts` (not `main.now_ts`)."""
import catalog
import charge
import pytest
import util


class Battery:
    """Stand-in for renault-api's battery-status object (attr access only here)."""

    def __init__(self, soc, power=0.0, energy=None):
        self.batteryLevel = soc
        self.chargingInstantaneousPower = power
        self.batteryAvailableEnergy = energy


# --------------------------------------------------------------------------- #
# inferred charge-session tracking
# --------------------------------------------------------------------------- #
def test_charge_session_lifecycle(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(charge, "now_ts", lambda: clock["t"])
    state = {}
    charge.update_charge_session(state, Battery(40, 7.0, 20.0), 52.0, charging=True)
    assert state["session_active"] is True
    charge.update_charge_session(state, Battery(60, 7.0, 30.0), 52.0, charging=True)
    clock["t"] = 1000.0 + 1800
    lc = charge.update_charge_session(state, Battery(80, 0.0, 40.0), 52.0, charging=False)
    assert state["session_active"] is False
    assert lc["last_charge_duration_min"] == 30
    assert lc["last_charge_recovered_pct"] == 40
    assert lc["last_charge_recovered_kwh"] == 20.0
    assert lc["last_charge_average_power"] == 7.0
    assert lc["last_charge_type"] == "Home"


def test_charge_session_energy_falls_back_to_soc_estimate(monkeypatch):
    monkeypatch.setattr(charge, "now_ts", lambda: 0.0)
    state = {}
    charge.update_charge_session(state, Battery(50, 7.0, None), 52.0, charging=True)
    assert state["start_energy"] == pytest.approx(26.0)


def test_rapid_charge_is_classified_public(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(charge, "now_ts", lambda: clock["t"])
    state = {}
    charge.update_charge_session(state, Battery(20, 50.0, 10.0), 52.0, charging=True)
    clock["t"] = 1800
    lc = charge.update_charge_session(state, Battery(60, 0.0, 31.0), 52.0, charging=False)
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
    lc = charge._parse_charge_session([older, _CHARGE_ITEM], 52.0)
    assert lc["last_charge_end"] == "2026-06-21T02:00:00+00:00"
    assert lc["last_charge_start_soc"] == 30 and lc["last_charge_end_soc"] == 80
    assert lc["last_charge_recovered_pct"] == 50
    assert lc["last_charge_recovered_kwh"] == 26.0
    assert lc["last_charge_duration_min"] == 240          # from timestamps, not chargeDuration
    assert lc["last_charge_average_power"] == 6.5         # 26 kWh / 4 h
    assert lc["last_charge_type"] == "Home"
    # produces exactly the Last Charge sensor keys (same contract as the inferred path)
    expected = {obj[len("r5_"):] for obj in catalog.SENSORS if "last_charge" in obj}
    assert set(lc) == expected


def test_parse_charge_session_empty_and_incomplete():
    assert charge._parse_charge_session([], 52.0) == {}
    assert charge._parse_charge_session(None, 52.0) == {}
    assert charge._parse_charge_session([{"chargeStartDate": "2026-06-21T22:00:00+00:00"}], 52.0) == {}


def test_parse_charge_session_derives_missing_energy_from_soc():
    item = {"chargeStartDate": "2026-06-21T00:00:00+00:00",
            "chargeEndDate": "2026-06-21T01:00:00+00:00",
            "chargeStartBatteryLevel": 20, "chargeEndBatteryLevel": 40}
    lc = charge._parse_charge_session([item], 50.0)
    assert lc["last_charge_recovered_pct"] == 20          # 40 - 20
    assert lc["last_charge_recovered_kwh"] == 10.0        # 20% of 50 kWh


def test_prefer_real_charge_matches_same_session_within_tolerance():
    real = {"last_charge_end": "2026-06-21T02:00:00+00:00"}
    assert charge._prefer_real_charge(real, {}) is True        # nothing inferred yet -> use endpoint
    assert charge._prefer_real_charge({}, real) is False       # no endpoint data -> keep inferred
    # endpoint's actual stop precedes the inferred (observed) stop by minutes -> same session,
    # authoritative record still wins (the bug codex caught: strict >= rejected this)
    live_observed_later = {"last_charge_end": "2026-06-21T02:05:00+00:00"}   # +5 min
    assert charge._prefer_real_charge(real, live_observed_later) is True
    # a live session ending materially later (hours) is a fresh charge not yet posted -> keep it
    live_fresh = {"last_charge_end": "2026-06-21T06:00:00+00:00"}            # +4 h
    assert charge._prefer_real_charge(real, live_fresh) is False
    assert charge._prefer_real_charge({"last_charge_end": "garbage"}, live_fresh) is False


def test_due_for_charges_throttle(monkeypatch):
    monkeypatch.setattr(charge, "now_ts", lambda: 10_000.0)
    assert charge._due_for_charges({}) is True
    assert charge._due_for_charges({"charges_last_fetch": 10_000.0}) is False
    assert charge._due_for_charges({"charges_last_fetch": 0.0}) is True
    assert charge._due_for_charges({"charges_last_fetch": 10_000.0, "charges_dirty": True}) is True


def test_epoch_none_for_empty_or_non_string():
    assert charge._epoch(None) is None
    assert charge._epoch("") is None
    assert charge._epoch(12345) is None          # non-string guard
    assert charge._epoch("not-a-date") is None


def test_prefer_real_charge_boundary_and_unparseable_live():
    real = {"last_charge_end": "2026-06-21T02:00:00+00:00"}
    re_ep = charge._epoch(real["last_charge_end"])
    # exactly on the tolerance boundary (live ends CHARGE_MATCH_TOLERANCE_SEC after real) -> same session
    at_boundary = {"last_charge_end": util.iso(re_ep + charge.CHARGE_MATCH_TOLERANCE_SEC)}
    assert charge._prefer_real_charge(real, at_boundary) is True
    # one second past the boundary -> a materially-later fresh session, keep the inferred one
    past = {"last_charge_end": util.iso(re_ep + charge.CHARGE_MATCH_TOLERANCE_SEC + 1)}
    assert charge._prefer_real_charge(real, past) is False
    # an unparseable INFERRED end can't out-date the endpoint -> endpoint wins (the le-is-None branch)
    assert charge._prefer_real_charge(real, {"last_charge_end": "garbage"}) is True
