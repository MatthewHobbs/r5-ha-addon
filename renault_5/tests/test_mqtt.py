"""Tests for the mqtt seam: HA MQTT-discovery publishing, the broker client wiring, and the
connect/message/disconnect callbacks. The discovery-template/data-key contract lives here —
it's the class of bug that has shipped broken dashboard tiles.

publish_discovery reads mqtt.PUBLISH_LOCATION in the mqtt namespace, so tests patch it on the
mqtt module. _on_message dispatches via the injected _COMMAND_HANDLER + _LOOP (main wires real
ones at startup); r5's mqtt has NO _MQTT_CTX (unlike its a290 twin)."""
import json

import catalog
import mqtt


def _obj(**attrs):
    return type("Obj", (), attrs)()


class StubClient:
    """Captures MQTT publishes so we can assert on discovery payloads."""

    def __init__(self):
        self.pub = {}

    def publish(self, topic, payload, retain=False):
        self.pub[topic] = payload


class _FakeClient:
    """Full broker-client stand-in for the connect/callback tests."""

    def __init__(self, *a, **k):
        self.subs, self.pubs = [], []

    def username_pw_set(self, u, p):
        self.creds = (u, p)

    def will_set(self, *a, **k):
        self.will = (a, k)

    def reconnect_delay_set(self, **k):
        self.delay = k

    def subscribe(self, topic):
        self.subs.append(topic)

    def publish(self, topic, payload="", retain=False):
        self.pubs.append((topic, payload))

    def connect(self, host, port, keepalive=60):
        self.conn = (host, port, keepalive)

    def loop_start(self):
        self.started = True


# --------------------------------------------------------------------------- #
# discovery template / data-key contract
# --------------------------------------------------------------------------- #
def test_sensor_value_templates_strip_the_prefix():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    for obj in catalog.SENSORS:
        payload = c.pub.get(f"homeassistant/sensor/{mqtt.NODE}/{obj}/config")
        assert payload, f"{obj} not published"
        conf = json.loads(payload)
        assert conf["value_template"] == "{{ value_json.%s }}" % obj.removeprefix("r5_")


def test_binary_sensor_value_templates_strip_the_prefix():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    for obj in catalog.BINARY_SENSORS:
        payload = c.pub.get(f"homeassistant/binary_sensor/{mqtt.NODE}/{obj}/config")
        assert payload, f"{obj} not published"
        conf = json.loads(payload)
        assert conf["value_template"] == "{{ value_json.%s }}" % obj.removeprefix("r5_")


def test_optional_sensors_cleared_when_unsupported():
    c = StubClient()
    mqtt.publish_discovery(c, set(), "km")
    for obj in catalog.OPTIONAL_ENDPOINTS["pressure"]:
        assert c.pub[f"homeassistant/sensor/{mqtt.NODE}/{obj}/config"] == ""


def test_distance_device_class_dropped_only_for_miles():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    for obj in ("r5_battery_autonomy", "r5_vehicle_mileage"):
        conf = json.loads(c.pub[f"homeassistant/sensor/{mqtt.NODE}/{obj}/config"])
        assert conf.get("device_class") == "distance" and conf["unit_of_measurement"] == "km"

    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "mi")
    for obj in ("r5_battery_autonomy", "r5_vehicle_mileage"):
        conf = json.loads(c.pub[f"homeassistant/sensor/{mqtt.NODE}/{obj}/config"])
        assert "device_class" not in conf and conf["unit_of_measurement"] == "mi"


def test_buttons_published_when_supported():
    c = StubClient()
    all_eps = {ep for _oid, _node, _name, _icon, ep in catalog.ACTION_BUTTONS.values()}
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS) | all_eps, "km")
    for _cmd, (oid, node, name, _icon, _ep) in catalog.ACTION_BUTTONS.items():
        conf = json.loads(c.pub[f"homeassistant/button/{mqtt.NODE}/{node}/config"])
        assert conf["name"] == name
        assert conf["object_id"] == oid


def test_buttons_cleared_when_action_endpoint_unsupported():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")   # no action endpoints
    for _cmd, (_oid, node, _name, _icon, _ep) in catalog.ACTION_BUTTONS.items():
        assert c.pub[f"homeassistant/button/{mqtt.NODE}/{node}/config"] == ""


def test_numbers_published_when_soc_supported():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS) | {catalog.SOC_ENDPOINT}, "mi")
    for obj, (name, _icon, _role, mn, mx, step) in catalog.NUMBERS.items():
        short = obj.removeprefix("r5_")
        conf = json.loads(c.pub[f"homeassistant/number/{mqtt.NODE}/{short}/config"])
        assert conf["name"] == name
        assert conf["command_topic"] == f"{mqtt.CMD_PREFIX}{short}"
        assert conf["value_template"] == "{{ value_json.%s }}" % short
        assert (conf["min"], conf["max"], conf["step"]) == (mn, mx, step)
        assert conf["device_class"] == "battery" and conf["unit_of_measurement"] == "%"


def test_numbers_cleared_when_soc_unsupported():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "mi")   # soc-levels not supported
    for obj in catalog.NUMBERS:
        assert c.pub[f"homeassistant/number/{mqtt.NODE}/{obj.removeprefix('r5_')}/config"] == ""


def test_retired_soc_sensors_are_cleared():
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS) | {catalog.SOC_ENDPOINT}, "mi")
    for obj in ("r5_soc_max_target", "r5_soc_min_target"):
        assert c.pub[f"homeassistant/sensor/{mqtt.NODE}/{obj}/config"] == ""


# --------------------------------------------------------------------------- #
# location publishing (opt-out)
# --------------------------------------------------------------------------- #
def test_publish_discovery_location_enabled_vs_disabled(monkeypatch):
    tracker_topic = f"{mqtt.DISCOVERY_PREFIX}/device_tracker/{mqtt.NODE}/location/config"

    # enabled (default): a populated device_tracker config is published
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    assert '"source_type": "gps"' in c.pub[tracker_topic]

    # disabled: the tracker is cleared AND the retained GPS topics are wiped off the broker
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", False)
    c = StubClient()
    mqtt.publish_discovery(c, set(catalog.OPTIONAL_ENDPOINTS), "km")
    assert c.pub[tracker_topic] == ""
    assert c.pub[mqtt.ATTR_TOPIC] == ""
    assert c.pub[mqtt.TRACKER_STATE_TOPIC] == ""


def test_refresh_location_button_cleared_when_location_disabled(monkeypatch):
    (cmd,) = tuple(k for k, v in catalog.ACTION_BUTTONS.items()
                   if v[-1] == catalog.REFRESH_LOCATION_EP)
    _oid, node, _name, _icon, _ep = catalog.ACTION_BUTTONS[cmd]
    btn_topic = f"{mqtt.DISCOVERY_PREFIX}/button/{mqtt.NODE}/{node}/config"
    eps = set(catalog.OPTIONAL_ENDPOINTS) | {catalog.REFRESH_LOCATION_EP}

    # location on: the refresh-location button is published
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", True)
    c = StubClient()
    mqtt.publish_discovery(c, eps, "km")
    assert "command_topic" in c.pub[btn_topic]

    # location off: the button is cleared even though the endpoint is supported
    monkeypatch.setattr(mqtt, "PUBLISH_LOCATION", False)
    c = StubClient()
    mqtt.publish_discovery(c, eps, "km")
    assert c.pub[btn_topic] == ""


# --------------------------------------------------------------------------- #
# broker client + callbacks
# --------------------------------------------------------------------------- #
def test_on_message_dispatches(monkeypatch):
    captured = {}
    monkeypatch.setattr(mqtt, "_LOOP", object())
    monkeypatch.setattr(mqtt, "_COMMAND_HANDLER", lambda cmd, payload="": ("CO", cmd, payload))
    monkeypatch.setattr(mqtt.asyncio, "run_coroutine_threadsafe",
                        lambda coro, loop: captured.update(coro=coro, loop=loop))
    mqtt._on_message(None, None, _obj(topic=mqtt.CMD_PREFIX + "horn", payload=b""))
    assert captured["coro"] == ("CO", "horn", "")
    mqtt._on_message(None, None, _obj(topic=mqtt.CMD_PREFIX + "soc_max_target", payload=b"90"))
    assert captured["coro"] == ("CO", "soc_max_target", "90")


def test_on_message_ignores_non_command_and_no_loop(monkeypatch):
    fired = {"n": 0}
    monkeypatch.setattr(mqtt.asyncio, "run_coroutine_threadsafe",
                        lambda *a, **k: fired.__setitem__("n", fired["n"] + 1))
    monkeypatch.setattr(mqtt, "_COMMAND_HANDLER", lambda *a, **k: None)
    monkeypatch.setattr(mqtt, "_LOOP", object())
    mqtt._on_message(None, None, _obj(topic="some/other/topic"))       # wrong prefix
    monkeypatch.setattr(mqtt, "_LOOP", None)
    mqtt._on_message(None, None, _obj(topic=mqtt.CMD_PREFIX + "horn"))  # loop not ready
    assert fired["n"] == 0


def test_on_connect_subscribes_and_announces():
    c = _FakeClient()
    mqtt._on_connect(c, None, None, 0)
    assert c.subs == [f"{mqtt.CMD_PREFIX}#"]
    assert (mqtt.AVAIL_TOPIC, "online") in c.pubs
    refused = _FakeClient()
    mqtt._on_connect(refused, None, None, 5)
    assert refused.subs == [] and refused.pubs == []


def test_on_disconnect_logs_both_paths():
    mqtt._on_disconnect(None, None, None, 0)   # clean
    mqtt._on_disconnect(None, None, None, 1)   # unexpected (warns)


def test_mqtt_connect(monkeypatch):
    holder = {}

    def factory(*a, **k):
        c = _FakeClient(*a, **k)
        holder["c"] = c
        return c

    monkeypatch.setattr(mqtt.paho_mqtt, "Client", factory)
    monkeypatch.setenv("MQTT_USER", "u")
    monkeypatch.setenv("MQTT_PASS", "p")
    monkeypatch.setenv("MQTT_HOST", "broker")
    monkeypatch.setenv("MQTT_PORT", "1884")
    client = mqtt.mqtt_connect()
    c = holder["c"]
    assert client is c
    assert c.creds == ("u", "p")
    assert c.conn == ("broker", 1884, 30)
    assert c.delay == {"min_delay": 1, "max_delay": 120}
    assert c.started is True
