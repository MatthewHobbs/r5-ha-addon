"""Conformance: the bundled dashboards and the UI-gate seed must reference entities this build
actually publishes.

A Lovelace card reading a non-existent entity renders a fallback, not an error, so a dashboard
can drift away from the catalog and every gate still passes: pytest never opens the ``.txt``
dashboards, and the Playwright gate seeds whatever ids the dashboards name and fails only on
truncation or ``hui-error-card``. That is how
`#50 <https://github.com/MatthewHobbs/r5-ha-addon/issues/50>`_ shipped: ``r5_soc_min_target`` and
``r5_soc_max_target`` moved from ``SENSORS`` to ``NUMBERS``, both dashboards kept reading
``sensor.*``, the seed kept seeding ``sensor.r5_soc_*``, and every install showed empty Min/Max
SOC badges for weeks.

ENTITY IDS COME FROM NAMES, NOT OBJECT_IDS. Home Assistant ignores the discovery ``object_id``
and derives ``entity_id = slug(device name + " " + entity name)``: the object_id
``r5_charge_start`` is ``button.r5_start_charging``. References are matched on the device-slug
prefix, the object_id prefix and the brand word (all ``r5_`` on this model; the A290 twin, where
they differ, shares this code), so an object_id-shaped id fails rather than being skipped. The
ids are taken from the discovery configs the shared core's real ``publish_discovery`` emits,
which also covers the core-published device_tracker without a hand-kept list.

Pure string/AST work plus one in-process discovery run: no HA, no browser, no network.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import catalog
import main  # noqa: F401  -- importing main runs mqtt.configure(catalog)
import pytest
from renault_mqtt import mqtt

_REPO = Path(__file__).resolve().parents[2]
_DASHBOARDS = sorted((_REPO / "renault_5" / "dashboards").glob("*.txt"))
_SEED = _REPO / "ui-tests" / "seed.py"


def _slug(text: str) -> str:
    """homeassistant.util.slugify for ASCII input (python-slugify: lowercase, every
    non-alphanumeric run -> one "_", trim). Apostrophes become a separator, not nothing:
    "Driver's Seat" -> driver_s_seat. HA transliterates non-ASCII first, which this does not;
    test_entity_names_keep_the_slug_valid keeps the inputs where the two agree."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


_DEVICE_SLUG = _slug(catalog.DEVICE["name"])  # "r5"
_DOMAINS = ("sensor", "binary_sensor", "number", "button", "device_tracker",
            "input_boolean", "input_button", "input_number", "input_datetime", "input_text")
# Entity-id prefix, object_id prefix, and the brand word. Matching only the entity-id prefix
# would silently skip an id written as an object_id wherever the two differ.
_PREFIXES = sorted({_DEVICE_SLUG + "_", catalog.OBJ_PREFIX, _DEVICE_SLUG.split("_")[0] + "_"})
_REF = re.compile(r"\b(" + "|".join(_DOMAINS) + r")\.((?:" + "|".join(_PREFIXES) + r")[a-z0-9_]+)")

# Referenced but never published by the add-on. The A290 twin derives this set from the helper
# packages it ships (Packages/, Templates/); this add-on ships none (README "Optional": users
# port them from upstream), so it is hand-kept. Each entry needs a reason, and
# test_allowlist_has_no_stale_entries removes the ones that stop being true.
_NOT_PUBLISHED = {
    # The optional test-mode preview package (README "Optional"): user-installed HA helpers and
    # template sensors, never published by the add-on.
    "input_boolean.r5_test_mode",
    "input_button.r5_test_charge_run",
    "binary_sensor.r5_test_show_panel",
    "sensor.r5_test_ends_countdown",
    "sensor.r5_test_panel_hide_countdown",
    # The optional "pretty location" user template sensor (README "Optional").
    "sensor.r5_pretty_location",
}


class _Recorder:
    """Keeps the last payload per topic, i.e. what the broker would retain."""

    def __init__(self) -> None:
        self.retained: dict[str, str] = {}

    def publish(self, topic, payload, retain=False):
        self.retained[topic] = payload


@pytest.fixture
def published(monkeypatch) -> set[str]:
    """Every entity_id this build can publish, as Home Assistant would name it.

    Runs the core's real publish_discovery with every optional capability on (all endpoints
    supported, location and the opt-in refresh button enabled), so this is the most this build
    can ever publish; a reference outside it cannot resolve on any car.
    """
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    monkeypatch.setattr(mqtt, "ENABLE_REFRESH_LOCATION", True)
    every_ep = (set(catalog.OPTIONAL_ENDPOINTS) | {catalog.SOC_ENDPOINT}
                | {ep for *_, ep in catalog.ACTION_BUTTONS.values()})
    rec = _Recorder()
    mqtt.publish_discovery(rec, every_ep, "km")
    ids = set()
    for topic, payload in rec.retained.items():
        parts = topic.split("/")
        if parts[0] != mqtt.DISCOVERY_PREFIX or parts[-1] != "config" or not payload:
            continue          # state/attribute topics, and cleared (tombstoned) configs
        conf = json.loads(payload)
        ids.add(f"{parts[1]}.{_slug(conf['device']['name'] + ' ' + conf['name'])}")
    return ids


def _dashboard_refs() -> set[tuple[str, str]]:
    """(source file, entity_id) for every add-on-prefixed entity the dashboards reference."""
    found = set()
    for path in _DASHBOARDS:
        for domain, rest in _REF.findall(path.read_text(encoding="utf-8")):
            found.add((path.name, f"{domain}.{rest}"))
    return found


def _seeded() -> set[str]:
    """entity_id for every prefixed entity the UI gate seeds by name.

    Parsed with ``ast`` rather than imported: seed.py needs aiohttp and a live HA to run.
    """
    tree = ast.parse(_SEED.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _REF.fullmatch(node.value)
    }


def _elsewhere(eid: str, published: set[str]) -> list[str]:
    """The same name published under another domain, if any."""
    name = eid.split(".", 1)[1]
    return sorted(p for p in published if p.split(".", 1)[1] == name and p != eid)


def test_inputs_are_not_empty(published) -> None:
    """Guard the guard: a glob, pattern or discovery run that matches nothing would pass every
    test below."""
    assert {p.name for p in _DASHBOARDS} >= {"front-end.txt", "front-end-bubble.txt"}
    per_file = {p.name: 0 for p in _DASHBOARDS}
    for name, _ in _dashboard_refs():
        per_file[name] += 1
    assert all(n >= 20 for n in per_file.values()), per_file
    assert len(_seeded()) >= 20, sorted(_seeded())
    assert len(published) >= 20, sorted(published)


def test_entity_ids_are_derived_from_names(published) -> None:
    """Guard the derivation: a slug or discovery change here would silently re-open the gap."""
    assert "sensor.r5_battery_level" in published             # name slug == object_id tail
    assert "device_tracker.r5_location" in published          # core-published, not in catalog
    assert "button.r5_refresh_location" in published          # opt-in, forced on above
    # object_id tail != name slug: only the name-derived form is a real id.
    assert "button.r5_start_charging" in published
    assert "button.r5_charge_start" not in published
    assert "sensor.r5_outside_temperature" in published
    assert "sensor.r5_external_temperature" not in published
    # Retired sensors (tombstoned) are gone; the SoC limits live on as numbers (#50).
    for obj in catalog.RETIRED_SENSORS:
        assert f"number.{obj}" in published
        assert f"sensor.{obj}" not in published


def test_entity_names_keep_the_slug_valid() -> None:
    """The derivation holds only for ASCII names (HA transliterates, _slug does not), and only
    while no entity name starts with the device name (HA's MQTT integration strips that prefix)."""
    names = [meta[0] for table in (catalog.SENSORS, catalog.BINARY_SENSORS, catalog.ACTION_BUTTONS,
                                    catalog.NUMBERS) for meta in table.values()]
    assert all(n.isascii() for n in [catalog.DEVICE["name"], *names]), [n for n in names if not n.isascii()]
    device = catalog.DEVICE["name"].lower()
    assert not [n for n in names if n.lower().startswith(device)]


def test_dashboard_entities_exist(published) -> None:
    """Every dashboard entity is one this build publishes, or a documented user helper."""
    unknown = sorted(
        f"{src}: {eid}"
        for src, eid in _dashboard_refs()
        if eid not in published and eid not in _NOT_PUBLISHED and not _elsewhere(eid, published)
    )
    assert not unknown, (
        "Dashboards reference entity ids this build does not publish. Home Assistant names "
        "entities slug(device name + entity NAME), so check the catalog's names, not its "
        "object_ids. If the entity is a user helper, add it to _NOT_PUBLISHED with a reason:\n  "
        + "\n  ".join(unknown)
    )


def test_dashboard_entities_use_the_domain_the_catalog_publishes(published) -> None:
    """The #50 regression: right name, wrong domain, renders a silent fallback."""
    wrong = sorted(
        f"{src}: {eid} — published as {', '.join(_elsewhere(eid, published))}"
        for src, eid in _dashboard_refs()
        if eid not in published and _elsewhere(eid, published)
    )
    assert not wrong, "Dashboard entity domains disagree with what is published:\n  " + "\n  ".join(wrong)


def test_dashboards_do_not_reference_retired_sensors() -> None:
    """RETIRED_SENSORS configs are actively cleared, so these entities cannot exist.

    Independent of the published derivation above. RETIRED_SENSORS holds object_ids; both
    entries were named so that their old entity_id equalled the object_id.
    """
    retired = {f"sensor.{obj}" for obj in catalog.RETIRED_SENSORS}
    offenders = sorted(f"{src}: {eid}" for src, eid in _dashboard_refs() if eid in retired)
    assert not offenders, (
        "Dashboards reference sensors listed in RETIRED_SENSORS, whose discovery configs are "
        "cleared on every startup — these entities do not exist:\n  " + "\n  ".join(offenders)
    )


def test_allowlist_has_no_stale_entries(published) -> None:
    """An allowlist entry that is now published would collide on install and exempt nothing
    real; one no longer referenced is hiding nothing today and will hide the next drift."""
    referenced = {eid for _, eid in _dashboard_refs()}
    assert not _NOT_PUBLISHED & published, sorted(_NOT_PUBLISHED & published)
    assert _NOT_PUBLISHED <= referenced, sorted(_NOT_PUBLISHED - referenced)


def test_ui_gate_seeds_what_the_add_on_publishes(published) -> None:
    """The seed must not invent entities, or the UI gate validates a fiction (#50 was seeded)."""
    bad = []
    for eid in sorted(_seeded()):
        if eid in published or eid in _NOT_PUBLISHED:
            continue
        other = _elsewhere(eid, published)
        bad.append(f"{eid} — published as {', '.join(other)}" if other else f"{eid} — not published")
    assert not bad, (
        "ui-tests/seed.py seeds entities that differ from what the add-on publishes, so the "
        "UI gate renders against an entity set no real install has:\n  " + "\n  ".join(bad)
    )
