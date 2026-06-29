"""Tests for the optional dashboard auto-deploy's Smart Charging injection.

Mirrors the A290 add-on's suite (the two share this dashboard layer): the standard-dashboard
Mushroom block, the bubble pop-up "tab", and the main-menu restructure.
"""
import asyncio

import deploy


# --------------------------------------------------------------------------- #
# _charger_cards — optional standard-dashboard "Smart Charging" block (Mushroom cards)
# --------------------------------------------------------------------------- #
def test_charger_cards_none_when_no_entities_set(monkeypatch):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    assert deploy._charger_cards() is None


def test_charger_cards_match_dashboard_style(monkeypatch):
    monkeypatch.setenv("R5_CHARGER_SMART_CHARGE", "switch.octopus_intelligent_smart_charge")
    monkeypatch.setenv("R5_CHARGER_BUMP_CHARGE", "")        # blank -> skipped
    monkeypatch.setenv("R5_CHARGER_TARGET_SOC", "number.octopus_intelligent_charge_target")
    monkeypatch.setenv("R5_CHARGER_TARGET_TIME", "select.octopus_intelligent_target_time")
    cards = deploy._charger_cards()
    assert cards[0]["type"] == "heading" and cards[0]["heading"] == "Smart Charging"
    controls = cards[1:]
    # every control is a Mushroom card (so the number renders as a value, not a light MDC box)
    assert all(c["type"] == "custom:mushroom-entity-card" for c in controls)
    by_entity = {c["entity"]: c for c in controls}
    assert set(by_entity) == {"switch.octopus_intelligent_smart_charge",
                              "number.octopus_intelligent_charge_target",
                              "select.octopus_intelligent_target_time"}   # blank bump skipped
    # the charge-target number is a plain value card, not an MDC input
    assert "mushroom-shape-icon$" in by_entity["number.octopus_intelligent_charge_target"]["card_mod"]["style"]
    # the switch stays one-tap (toggle), the number/select open more-info (default)
    assert by_entity["switch.octopus_intelligent_smart_charge"]["tap_action"]["action"] == "toggle"
    assert "tap_action" not in by_entity["number.octopus_intelligent_charge_target"]


def test_charger_cards_include_offpeak_badge(monkeypatch):
    monkeypatch.setenv("R5_CHARGER_DISPATCHING", "binary_sensor.disp")
    cards = deploy._charger_cards()
    badge = next(c for c in cards if c.get("type") == "custom:mushroom-template-card")
    # the rate state is conveyed by text (primary) + icon shape, not colour alone
    assert "Off-peak" in badge["primary"] and "Peak rate" in badge["primary"]
    # the window sub-line has a non-empty fallback so it never renders blank
    assert "{% else %}Schedule unavailable{% endif %}" in badge["secondary"]


def test_fetch_dashboard_adds_charger_block_when_configured(tmp_path, monkeypatch):
    (tmp_path / "front-end.txt").write_text("- title: Home\n  cards: []\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    monkeypatch.setenv("R5_CHARGER_SMART_CHARGE", "switch.x")
    cfg = asyncio.run(deploy._fetch_dashboard("standard"))
    assert cfg["title"] == "Renault 5"
    assert any(c.get("type") == "heading" and c.get("heading") == "Smart Charging"
               for c in cfg["views"][0]["cards"])


def test_fetch_dashboard_no_charger_block_when_unset(tmp_path, monkeypatch):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    (tmp_path / "front-end.txt").write_text("- title: Home\n  cards: []\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    cfg = asyncio.run(deploy._fetch_dashboard("standard"))
    assert cfg["views"][0]["cards"] == []                    # nothing added


def test_add_cards_inserts_beneath_presets_heading():
    # standard dashboard: the block goes directly after the Climate/Charging Presets section —
    # i.e. immediately before the next heading, not at the end of the section.
    view = {"type": "sections", "sections": [{"type": "grid", "cards": [
        {"type": "heading", "heading": "Climate/Charging Presets"},
        {"type": "tile", "entity": "x"},
        {"type": "heading", "heading": "Last Charge"},
        {"type": "tile", "entity": "y"},
    ]}]}
    new_cards = [{"type": "heading", "heading": "Smart Charging"}, {"type": "a"}]
    deploy._add_cards(view, new_cards)
    cards = view["sections"][0]["cards"]
    assert cards[2:4] == new_cards                # inserted before the "Last Charge" heading
    assert cards[4]["heading"] == "Last Charge"


def test_add_cards_cards_layout():
    # a plain `cards` view — the cards are appended to cards
    view = {"cards": [{"type": "x"}]}
    deploy._add_cards(view, [{"type": "heading", "heading": "Smart Charging"}])
    assert view["cards"][-1]["heading"] == "Smart Charging"


# --------------------------------------------------------------------------- #
# bubble dashboard — Smart Charging pop-up + main-menu restructure
# --------------------------------------------------------------------------- #
def test_charger_popup_none_when_unset(monkeypatch):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    assert deploy._charger_popup() is None


def _flat_popup_cards(pop):
    out = {}
    for c in pop["cards"]:
        for inner in (c["cards"] if c.get("type") == "horizontal-stack" else [c]):
            if "entity" in inner:
                out[inner["entity"]] = inner
    return out


def test_charger_popup_builds_native_controls(monkeypatch):
    monkeypatch.setenv("R5_CHARGER_SMART_CHARGE", "switch.smart")
    monkeypatch.setenv("R5_CHARGER_BUMP_CHARGE", "switch.bump")
    monkeypatch.setenv("R5_CHARGER_TARGET_SOC", "number.soc")
    monkeypatch.setenv("R5_CHARGER_TARGET_TIME", "select.ttime")
    monkeypatch.setenv("R5_CHARGER_DISPATCHING", "binary_sensor.disp")
    pop = deploy._charger_popup()
    assert pop["card_type"] == "pop-up" and pop["hash"] == deploy._CHARGER_HASH
    # smart + bump share one horizontal-stack row (compact toggles)
    assert any(c.get("type") == "horizontal-stack" and len(c["cards"]) == 2 for c in pop["cards"])
    by_entity = _flat_popup_cards(pop)
    # toggles match the dashboard's other command buttons (dark pill + icon, not a blue fill)
    assert by_entity["switch.smart"]["button_type"] == "name"
    assert by_entity["switch.smart"]["button_action"]["tap_action"]["action"] == "toggle"
    assert by_entity["number.soc"]["button_type"] == "slider"       # charge target slider
    assert by_entity["number.soc"]["show_state"] is True            # shows the %
    assert "FFD60A" in by_entity["number.soc"]["styles"]            # 80% recommendation marker
    assert by_entity["select.ttime"]["card_type"] == "select"       # target time dropdown
    # off-peak badge: a Mushroom template card showing the current rate + the window times
    badge = next(c for c in pop["cards"] if c.get("type") == "custom:mushroom-template-card")
    assert "Off-peak" in badge["primary"] and "Peak rate" in badge["primary"]
    assert "next_start" in badge["secondary"] and "%H:%M" in badge["secondary"]


def _flat_menu_names(menu):
    out = []
    for item in menu["cards"]:
        if item.get("type") == "horizontal-stack":
            out.extend(c["name"] for c in item["cards"])
        else:
            out.append(item["name"])
    return out


def _bubble_menu_view():
    def btn(name):
        return {"type": "custom:bubble-card", "card_type": "button", "button_type": "name",
                "name": name}
    menu = {"type": "custom:bubble-card", "card_type": "pop-up", "hash": "#r5", "cards": [
        {"type": "horizontal-stack", "cards": [btn("Vehicle Status"), btn("Charge Status")]},
        {"type": "horizontal-stack", "cards": [btn("Activity"), btn("Last Charge")]},
        btn("Diagnostics"),
        btn("Location"),
    ]}
    return {"cards": [menu]}


def test_inject_bubble_charging_button_popup_and_location_full_width(monkeypatch):
    monkeypatch.setenv("R5_CHARGER_SMART_CHARGE", "switch.smart")
    view = _bubble_menu_view()
    deploy._inject_bubble_charging(view)
    assert any(c.get("hash") == deploy._CHARGER_HASH for c in view["cards"])   # pop-up added
    menu = view["cards"][0]
    assert "Smart Charging" in _flat_menu_names(menu)                          # menu button
    assert menu["cards"][-1]["name"] == "Location"                            # last item
    assert menu["cards"][-1]["type"] == "custom:bubble-card"                  # full-width btn


def test_inject_bubble_charging_noop_when_unset(monkeypatch):
    for env, _ in deploy._CHARGER_ENTITIES:
        monkeypatch.delenv(env, raising=False)
    view = _bubble_menu_view()
    deploy._inject_bubble_charging(view)
    assert len(view["cards"]) == 1                                            # no pop-up
    assert "Smart Charging" not in _flat_menu_names(view["cards"][0])         # menu untouched


def test_fetch_dashboard_bubble_injects_popup(tmp_path, monkeypatch):
    (tmp_path / "front-end-bubble.txt").write_text(
        "- title: R5\n"
        "  cards:\n"
        "    - type: custom:bubble-card\n"
        "      card_type: pop-up\n"
        "      hash: '#r5'\n"
        "      cards:\n"
        "        - type: horizontal-stack\n"
        "          cards:\n"
        "            - {type: custom:bubble-card, card_type: button, name: Charge Status}\n"
        "            - {type: custom:bubble-card, card_type: button, name: Location}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "DASHBOARD_DIR", str(tmp_path))
    monkeypatch.setenv("R5_CHARGER_SMART_CHARGE", "switch.smart")
    cfg = asyncio.run(deploy._fetch_dashboard("bubble"))
    assert any(c.get("hash") == deploy._CHARGER_HASH for c in cfg["views"][0]["cards"])
