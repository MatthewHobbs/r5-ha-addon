"""Conformance: the bundled dashboards and the UI-gate seed must reference entities
this build actually publishes.

The add-on defines its entities in ``catalog.py`` and ships dashboards that consume
them. Nothing bound the two together, so a migration could move an entity and leave every
consumer behind — silently, because a Lovelace card reading a non-existent entity renders a
fallback rather than an error.

That is exactly what happened in
`#50 <https://github.com/MatthewHobbs/r5-ha-addon/issues/50>`_: ``r5_soc_min_target`` and
``r5_soc_max_target`` moved from ``SENSORS`` to ``NUMBERS`` (so users could set them) and were
added to ``RETIRED_SENSORS``, but both dashboards kept reading ``sensor.*``. Every install
after that migration showed empty Min/Max SOC badges. It went unreported for weeks and, when
reported, sat unresolved — because no gate could see it:

- ``pytest`` never opened the dashboards; they are ``.txt`` files.
- The Playwright UI gate *seeded* ``sensor.r5_soc_*``, so it rendered against an entity set
  production does not publish and passed on every run.
- Even with a correct seed it would still pass: that gate fails on text truncation and
  ``hui-error-card``, and a missing entity renders neither.

ENTITY IDS COME FROM NAMES, NOT OBJECT_IDS. Home Assistant ignores the discovery
``object_id`` and derives ``entity_id = slug(device name + " " + entity name)``. The first
version of this file compared against object_ids, so it would have accepted
``button.r5_charge_start`` (the real id is ``button.r5_start_charging``) and
``sensor.r5_external_temperature`` (really ``sensor.r5_outside_temperature``), and it needed
allowlist entries — with wrong reasons — for ids that are simply name-derived. The ids here
are taken from the discovery configs the shared core actually publishes, so the tracker and
any future core-published entity are covered without a hand-kept list.

Pure string/AST work plus one in-process discovery run: no HA, no browser, no network.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import catalog
import pytest
from renault_mqtt import mqtt

_REPO = Path(__file__).resolve().parents[2]
_DASHBOARDS = sorted((_REPO / "renault_5" / "dashboards").glob("*.txt"))
_SEED = _REPO / "ui-tests" / "seed.py"


def _slug(text: str) -> str:
    """homeassistant.util.slugify for ASCII input (python-slugify: drop apostrophes, lowercase,
    every non-alphanumeric run -> one "_", trim). HA transliterates non-ASCII first, which this
    does not — test_entity_names_are_ascii keeps the inputs inside the range where they agree."""
    text = text.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


# Domains an entity reference can carry that this add-on is responsible for publishing.
# input_* are user-created helpers (test-mode toggles) and are deliberately excluded.
_PUBLISHED_DOMAINS = ("sensor", "binary_sensor", "number", "button", "device_tracker")
_ID_PREFIX = _slug(catalog.DEVICE["name"]) + "_"
_REF = re.compile(r"\b(" + "|".join(_PUBLISHED_DOMAINS) + r")\.(" + _ID_PREFIX + r"[a-z0-9_]+)")

# Referenced by the dashboards but not published by the add-on at all. Each needs a reason —
# an unexplained entry here is how the next drift hides; test_allowlist_has_no_stale_entries
# removes the ones that stop being true.
_NOT_PUBLISHED = {
    # The optional test-mode preview package (README "Optional"), a user-installed HA
    # helper/template package, never published by the add-on.
    "binary_sensor.r5_test_show_panel",
    "sensor.r5_test_ends_countdown",
    "sensor.r5_test_panel_hide_countdown",
    # The optional "pretty location" user template sensor (README "Optional").
    "sensor.r5_pretty_location",
}

# Seeded by ui-tests/seed.py but not a real entity id. Kept separate from _NOT_PUBLISHED so
# the dashboards cannot start leaning on it. Each entry must still be in the seed and still be
# unpublished, so fixing seed.py forces the entry out.
_SEED_KNOWN_DRIFT: set[str] = set()


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
    supported, location enabled), so this is the most this build can ever publish; a
    reference outside it cannot resolve on any car.
    """
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    monkeypatch.setattr(mqtt, "ENABLE_REFRESH_LOCATION", True)   # opt-in since 1.7.0; the tile is deploy-gated
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
    """entity_id for every prefixed entity the UI gate seeds.

    Parsed with ``ast`` rather than imported: seed.py talks to a live HA on import.
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


def test_dashboards_are_not_empty() -> None:
    """Guard the guard: a glob that silently matches nothing would pass every test below."""
    assert _DASHBOARDS, "no dashboards found — has the path moved?"
    assert _dashboard_refs(), "no entity references parsed — has the reference syntax changed?"


def test_entity_ids_are_derived_from_names(published) -> None:
    """Guard the derivation: a slug or discovery change here would silently re-open the gap."""
    assert "sensor.r5_battery_level" in published            # name slug == object_id
    assert "device_tracker.r5_location" in published         # core-published, not in catalog
    # object_id != name slug: only the name-derived form is a real id.
    assert "button.r5_start_charging" in published
    assert "button.r5_charge_start" not in published
    assert "sensor.r5_outside_temperature" in published
    assert "sensor.r5_external_temperature" not in published


def test_entity_names_are_ascii() -> None:
    """HA transliterates non-ASCII names before slugging; _slug does not, so keep names ASCII."""
    names = [catalog.DEVICE["name"]] + [
        meta[0]
        for table in (catalog.SENSORS, catalog.BINARY_SENSORS, catalog.ACTION_BUTTONS, catalog.NUMBERS)
        for meta in table.values()
    ]
    assert all(n.isascii() for n in names), [n for n in names if not n.isascii()]


def test_dashboard_entities_exist(published) -> None:
    """Every dashboard entity is one this build publishes, or a documented exception."""
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

    RETIRED_SENSORS holds object_ids; both retired entries were named so that their old
    entity_id equalled the object_id, which is what makes sensor.<object_id> the right key.
    """
    retired = {f"sensor.{obj}" for obj in catalog.RETIRED_SENSORS}
    offenders = sorted(f"{src}: {eid}" for src, eid in _dashboard_refs() if eid in retired)
    assert not offenders, (
        "Dashboards reference sensors listed in RETIRED_SENSORS, whose discovery configs are "
        "cleared on every startup — these entities do not exist:\n  " + "\n  ".join(offenders)
    )


def test_allowlist_has_no_stale_entries(published) -> None:
    """An allowlist entry that is now published, or no longer referenced, is hiding nothing
    today and will hide the next drift tomorrow."""
    referenced = {eid for _, eid in _dashboard_refs()}
    assert not _NOT_PUBLISHED & published, sorted(_NOT_PUBLISHED & published)
    assert _NOT_PUBLISHED <= referenced, sorted(_NOT_PUBLISHED - referenced)
    seeded = _seeded()
    assert not _SEED_KNOWN_DRIFT & published, sorted(_SEED_KNOWN_DRIFT & published)
    assert _SEED_KNOWN_DRIFT <= seeded, (
        "seed.py no longer seeds these — drop them from _SEED_KNOWN_DRIFT: "
        + ", ".join(sorted(_SEED_KNOWN_DRIFT - seeded))
    )


def test_ui_gate_seeds_what_the_add_on_publishes(published) -> None:
    """The seed must not invent entities, or the UI gate validates a fiction.

    This is the assertion that would have caught #50 first: the gate seeded
    ``sensor.r5_soc_*`` long after the add-on stopped publishing them.
    """
    bad = []
    for eid in sorted(_seeded() - _SEED_KNOWN_DRIFT):
        if eid in published or eid in _NOT_PUBLISHED:
            continue
        other = _elsewhere(eid, published)
        bad.append(f"{eid} — published as {', '.join(other)}" if other else f"{eid} — not published")
    assert not bad, (
        "ui-tests/seed.py seeds entities that differ from what the add-on publishes, so the "
        "UI gate renders against an entity set no real install has:\n  " + "\n  ".join(bad)
    )
