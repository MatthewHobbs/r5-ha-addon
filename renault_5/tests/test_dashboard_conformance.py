"""Conformance: the bundled dashboards and the UI-gate seed must reference entities
this build actually publishes.

The add-on defines entity object_ids in ``catalog.py`` and ships dashboards that consume
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

These tests close that gap at the only place it can be closed cheaply — the identifiers
themselves. They are pure string/AST work: no HA, no browser, no network.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import catalog

_REPO = Path(__file__).resolve().parents[2]
_DASHBOARDS = sorted((_REPO / "renault_5" / "dashboards").glob("*.txt"))
_SEED = _REPO / "ui-tests" / "seed.py"

# Domains an entity reference can carry that this add-on is responsible for publishing.
# input_* are user-created helpers (test-mode toggles) and are deliberately excluded.
_PUBLISHED_DOMAINS = ("sensor", "binary_sensor", "number", "button")
_REF = re.compile(
    r"\b(" + "|".join(_PUBLISHED_DOMAINS) + r")\.(" + catalog.OBJ_PREFIX + r"[a-z0-9_]+)"
)

# Referenced by the dashboards but not published from catalog.py. Each needs a reason —
# an unexplained entry here is how the next drift hides.
_NOT_FROM_CATALOG = {
    # Published by renault_mqtt.charge, not catalog.SENSORS — the charge-session
    # reconciliation moved to the shared engine and took its sensors with it.
    "r5_last_charge_duration",
    "r5_last_charge_energy_recovered",
    "r5_last_charge_soc_recovered",
    # Published by the shared engine's tracker discovery, not the sensor catalog.
    "r5_location",
    # Command button whose object_id is remapped via BUTTON_CMD_OVERRIDES.
    "r5_start_charging",
    # User-created template helpers backing the test-mode panel; documented in
    # docs/ and INSTALLATION.md, never published by the add-on.
    "r5_test_show_panel",
    "r5_test_ends_countdown",
    "r5_test_panel_hide_countdown",
    # A user template sensor (the "pretty location" pattern), not an add-on entity.
    "r5_pretty_location",
}


def _published() -> dict[str, str]:
    """object_id -> the domain catalog.py publishes it under."""
    domains: dict[str, str] = {}
    for obj in catalog.SENSORS:
        domains[obj] = "sensor"
    for obj in catalog.BINARY_SENSORS:
        domains[obj] = "binary_sensor"
    for obj in catalog.NUMBERS:
        domains[obj] = "number"
    for obj in catalog.ACTION_BUTTONS:
        domains[obj] = "button"
    return domains


def _dashboard_refs() -> set[tuple[str, str, str]]:
    """(source file, domain, object_id) for every entity the dashboards reference."""
    found = set()
    for path in _DASHBOARDS:
        text = path.read_text(encoding="utf-8")
        for domain, obj in _REF.findall(text):
            found.add((path.name, domain, obj))
    return found


def _seeded() -> set[tuple[str, str]]:
    """(domain, object_id) for every prefixed entity the UI gate seeds.

    Parsed with ``ast`` rather than imported: seed.py talks to a live HA on import.
    """
    tree = ast.parse(_SEED.read_text(encoding="utf-8"))
    keys = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    out = set()
    for key in keys:
        match = _REF.fullmatch(key)
        if match:
            out.add((match.group(1), match.group(2)))
    return out


def test_dashboards_are_not_empty() -> None:
    """Guard the guard: a glob that silently matches nothing would pass every test below."""
    assert _DASHBOARDS, "no dashboards found — has the path moved?"
    assert _dashboard_refs(), "no entity references parsed — has the reference syntax changed?"


def test_dashboard_entities_exist_in_the_catalog() -> None:
    """Every dashboard entity is one this build publishes, or a documented exception."""
    published = _published()
    unknown = sorted(
        f"{src}: {domain}.{obj}"
        for src, domain, obj in _dashboard_refs()
        if obj not in published and obj not in _NOT_FROM_CATALOG
    )
    assert not unknown, (
        "Dashboards reference entities this build does not publish. Either the entity was "
        "renamed/removed in catalog.py and the dashboard was not updated, or it is published "
        "elsewhere and belongs in _NOT_FROM_CATALOG with a reason:\n  "
        + "\n  ".join(unknown)
    )


def test_dashboard_entities_use_the_domain_the_catalog_publishes() -> None:
    """The #50 regression: right object_id, wrong domain, renders a silent fallback."""
    published = _published()
    wrong = sorted(
        f"{src}: {domain}.{obj} — catalog publishes it as {published[obj]}.{obj}"
        for src, domain, obj in _dashboard_refs()
        if obj in published and published[obj] != domain
    )
    assert not wrong, "Dashboard entity domains disagree with catalog.py:\n  " + "\n  ".join(wrong)


def test_dashboards_do_not_reference_retired_sensors() -> None:
    """RETIRED_SENSORS configs are actively cleared, so these entities cannot exist."""
    retired = set(catalog.RETIRED_SENSORS)
    offenders = sorted(
        f"{src}: sensor.{obj}"
        for src, domain, obj in _dashboard_refs()
        if domain == "sensor" and obj in retired
    )
    assert not offenders, (
        "Dashboards reference sensors listed in RETIRED_SENSORS, whose discovery configs are "
        "cleared on every startup — these entities do not exist:\n  " + "\n  ".join(offenders)
    )


def test_ui_gate_seeds_what_the_add_on_publishes() -> None:
    """The seed must not invent entities, or the UI gate validates a fiction.

    This is the assertion that would have caught #50 first: the gate seeded
    ``sensor.r5_soc_*`` long after the add-on stopped publishing them.
    """
    published = _published()
    bad = []
    for domain, obj in sorted(_seeded()):
        if obj in catalog.RETIRED_SENSORS and domain == "sensor":
            bad.append(f"{domain}.{obj} — listed in RETIRED_SENSORS; not published")
        elif obj in published and published[obj] != domain:
            bad.append(f"{domain}.{obj} — published as {published[obj]}.{obj}")
        elif obj not in published and obj not in _NOT_FROM_CATALOG:
            bad.append(f"{domain}.{obj} — not published by this build")
    assert not bad, (
        "ui-tests/seed.py seeds entities that differ from what the add-on publishes, so the "
        "UI gate renders against an entity set no real install has:\n  " + "\n  ".join(bad)
    )
