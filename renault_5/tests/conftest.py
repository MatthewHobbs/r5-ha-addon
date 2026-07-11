"""Put the add-on's app/ dir on sys.path so tests can `import main` / `import deploy`."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture(autouse=True)
def _isolate_module_globals():
    """Snapshot and restore main.py's mutable module-level singletons around every test.

    `_LATEST` (main) and `_DEBUG_STATE` (debug) are process-global dicts, and
    `_DISCOVERED_ACCOUNT_ID` (config) a process-global scalar, that several tests mutate;
    without this, a test that writes them leaks state into whatever runs next (order-dependent,
    and unsafe once tests run in parallel under pytest-xdist). Restoring here keeps each test
    isolated regardless of order. (r5 has no _MQTT_CTX, unlike its a290 twin.)"""
    import config
    import debug
    import main
    dict_globals = ((main, "_LATEST"), (debug, "_DEBUG_STATE"))
    scalar_globals = ((config, "_DISCOVERED_ACCOUNT_ID"),)
    saved_dicts = {(mod, name): copy.deepcopy(getattr(mod, name)) for mod, name in dict_globals}
    saved_scalars = {(mod, name): getattr(mod, name) for mod, name in scalar_globals}
    yield
    for (mod, name), value in saved_dicts.items():
        target = getattr(mod, name)
        target.clear()
        target.update(value)
    for (mod, name), value in saved_scalars.items():
        setattr(mod, name, value)
