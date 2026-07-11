"""Entity catalog for the Renault 5 — the declarative per-model tables (sensors, binary
sensors, icons, optional endpoints, control buttons, charge-limit numbers). Object_ids are
prefixed "r5_" (following the Topolino naming, minus the legacy _api/_mi suffixes); the
discovery value_template strips that prefix. Kept separate from main.py to match the Alpine
A290 add-on's structure (lockstep)."""

# The per-model object_id prefix. Every object_id below starts with it, and main.py strips it
# (obj.removeprefix(OBJ_PREFIX)) to derive the MQTT value_template key / command suffix. This is
# the ONE place the prefix is defined — the a290 twin sets "a290_" here and nothing else in the
# shared poll/publish loop changes.
OBJ_PREFIX = "r5_"

# The per-model environment-variable prefix the add-on's options are exported under (run.sh
# exports R5_USERNAME, R5_VIN, …). main.py injects it into the shared core as
# `config.ENV_PREFIX` so the redaction net reads this model's option names; the a290 twin sets
# "A290_" here. The ONE place the env prefix is defined.
ENV_PREFIX = "R5_"

# Per-model MQTT identity, injected into the shared core via mqtt.configure(catalog). NODE is the
# HA discovery node + topic root; DEVICE is the HA device block (its name drives the entity_id
# slug — HA ignores object_id); MQTT_KEEPALIVE is the broker keepalive. DIST_UNIT_OBJS names the
# sensors whose unit follows the locale (mi/km) instead of a fixed one. The a290 twin sets its own.
NODE = "renault_5"
DEVICE = {"identifiers": [NODE], "name": "R5", "manufacturer": "Renault", "model": "R5 E-Tech"}
MQTT_KEEPALIVE = 30
DIST_UNIT_OBJS = ("r5_battery_autonomy", "r5_vehicle_mileage")

# object_id -> (name, device_class, unit, state_class). Object_ids follow the Topolino
# R5 naming (minus the legacy _api/_mi suffixes); units are locale-aware (see publish).
SENSORS = {
    "r5_battery_level":          ("Battery Level", "battery", "%", "measurement"),
    "r5_battery_autonomy":       ("Battery Autonomy", "distance", "km", "measurement"),
    "r5_battery_temperature":    ("Battery Temperature", "temperature", "°C", "measurement"),
    "r5_charging_rate":          ("Charging Rate", "power", "kW", "measurement"),
    "r5_charging_remaining_time": ("Charging Remaining Time", "duration", "min", "measurement"),
    "r5_available_energy":       ("Available Energy", "energy_storage", "kWh", "measurement"),
    "r5_charger_plug_status":    ("Charger Plug Status", None, None, None),
    "r5_charger_status":         ("Charger Status", None, None, None),
    "r5_charging_flap_status":   ("Charging Flap Status", None, None, None),
    "r5_drive_side":             ("Drive Side", None, None, None),
    "r5_vehicle_mileage":        ("Vehicle Mileage", "distance", "km", "total_increasing"),
    "r5_preconditioning_temperature": ("Preconditioning Temperature", "temperature", "°C", None),
    "r5_hvac_last_activity":     ("HVAC Last Activity", "timestamp", None, None),
    "r5_gps_last_activity":      ("GPS Last Activity", "timestamp", None, None),
    "r5_external_temperature":   ("Outside Temperature", "temperature", "°C", "measurement"),
    "r5_cabin_temperature":      ("Cabin Temperature", "temperature", "°C", "measurement"),
    "r5_hvac_status":            ("HVAC Status", None, None, None),
    "r5_hvac_soc_threshold":     ("HVAC SoC Threshold", "battery", "%", None),
    "r5_charge_mode":            ("Charge Mode", None, None, None),
    "r5_charge_schedule_mode":   ("Charge Schedule Mode", None, None, None),
    "r5_scheduled_charge_start": ("Scheduled Charge Start", None, None, None),
    "r5_scheduled_charge_duration": ("Scheduled Charge Duration", "duration", "min", None),
    "r5_climate_schedule_mode":  ("Climate Schedule Mode", None, None, None),
    "r5_climate_ready_time":     ("Climate Ready Time", None, None, None),
    "r5_tyre_pressure_fl":       ("Tyre Pressure Front Left", None, None, "measurement"),
    "r5_tyre_pressure_fr":       ("Tyre Pressure Front Right", None, None, "measurement"),
    "r5_tyre_pressure_rl":       ("Tyre Pressure Rear Left", None, None, "measurement"),
    "r5_tyre_pressure_rr":       ("Tyre Pressure Rear Right", None, None, "measurement"),
    "r5_battery_last_activity":  ("Battery Last Activity", "timestamp", None, None),
    "r5_last_charge_start":          ("Last Charge Start", "timestamp", None, None),
    "r5_last_charge_end":            ("Last Charge End", "timestamp", None, None),
    "r5_last_charge_start_soc":      ("Last Charge Start SoC", "battery", "%", None),
    "r5_last_charge_end_soc":        ("Last Charge End SoC", "battery", "%", None),
    "r5_last_charge_start_energy":   ("Last Charge Start Energy", "energy", "kWh", None),
    "r5_last_charge_end_energy":     ("Last Charge End Energy", "energy", "kWh", None),
    "r5_last_charge_recovered_pct":  ("Last Charge SoC Recovered", None, "%", None),
    "r5_last_charge_recovered_kwh":  ("Last Charge Energy Recovered", "energy", "kWh", None),
    "r5_last_charge_duration_min":   ("Last Charge Duration", "duration", "min", None),
    "r5_last_charge_average_power":  ("Last Charge Average Power", "power", "kW", None),
    "r5_last_charge_type":           ("Last Charge Type", None, None, None),
}
# object_id -> (name, device_class)
BINARY_SENSORS = {
    "r5_charging":              ("Charging", "battery_charging"),
    "r5_heated_steering_wheel": ("Heated Steering Wheel", None),
    "r5_heated_seat_driver":    ("Heated Seat Driver", None),
    "r5_heated_seat_passenger": ("Heated Seat Passenger", None),
    "r5_plug_suspect":          ("Plug State Suspect", "problem"),
    "r5_api_auth_failure":      ("API Auth Failure", "problem"),
    "r5_data_stale":            ("Data Stale", "problem"),
}

# Icons for text/status sensors that would otherwise fall back to HA's generic mdi:eye.
ICONS = {
    "r5_charger_plug_status":   "mdi:power-plug",
    "r5_charger_status":        "mdi:battery-charging",
    "r5_charging_flap_status":  "mdi:ev-plug-type2",
    "r5_drive_side":            "mdi:steering",
    "r5_hvac_status":           "mdi:fan",
    "r5_charge_mode":           "mdi:ev-station",
    "r5_charge_schedule_mode":  "mdi:calendar-clock",
    "r5_scheduled_charge_start": "mdi:clock-start",
    "r5_scheduled_charge_duration": "mdi:timer-outline",
    "r5_climate_schedule_mode":  "mdi:fan-clock",
    "r5_climate_ready_time":     "mdi:clock-check-outline",
    "r5_last_charge_type":      "mdi:ev-station",
    "r5_heated_steering_wheel": "mdi:steering",
    "r5_heated_seat_driver":    "mdi:car-seat-heater",
    "r5_heated_seat_passenger": "mdi:car-seat-heater",
}

# Optional endpoints some models don't expose — gated on supports_endpoint().
OPTIONAL_ENDPOINTS = {
    "charge-mode": ["r5_charge_mode"],
    "pressure": ["r5_tyre_pressure_fl", "r5_tyre_pressure_fr",
                 "r5_tyre_pressure_rl", "r5_tyre_pressure_rr"],
}

# object_id -> (name, icon, action endpoint). Published only when supports_endpoint() is true,
# so a forbidden control is never shown. The discovery node segment + object_id derive from the
# object_id; the command suffix is object_id.removeprefix("r5_") unless remapped below.
ACTION_BUTTONS = {
    "r5_charge_start":          ("Start Charging", "mdi:ev-station", "actions/charge-start"),
    "r5_flash_lights":          ("Flash Lights", "mdi:car-light-high", "actions/lights-start"),
    "r5_sound_horn":            ("Sound Horn", "mdi:bullhorn", "actions/horn-start"),
    "r5_start_air_conditioner": ("Start Air Conditioner", "mdi:air-conditioner", "actions/hvac-start"),
    "r5_stop_air_conditioner":  ("Stop Air Conditioner", "mdi:fan-off", "actions/hvac-stop"),
    "r5_refresh_location":      ("Refresh Location", "mdi:crosshairs-gps", "actions/refresh-location"),
}
# object_id -> command suffix, where the command name differs from the object_id's short form.
# charge_start / refresh_location need none (cmd == short). Consumed by the core mqtt seam.
BUTTON_CMD_OVERRIDES = {
    "r5_flash_lights": "lights",
    "r5_sound_horn": "horn",
    "r5_start_air_conditioner": "hvac_start",
    "r5_stop_air_conditioner": "hvac_stop",
}

# Writable charge-limit controls. State comes from the poll's soc-levels read (data key =
# object_id without the r5_ prefix); a slider move writes via set_battery_soc(). Gated on
# SOC_ENDPOINT, so a model that rejects the write never ships the control. (name, icon, min,
# max, step); NUMBER_ROLES maps each object_id to its set_battery_soc arg ("min"/"target").
SOC_ENDPOINT = "soc-levels"
# (CHARGES_ENDPOINT — the authoritative recent-charge-sessions endpoint — moved to
# renault_mqtt.charge with the reconciliation logic; it's identical across models. main.py
# imports it from there for the endpoint-support probe.)
# The refresh-location action endpoint. Names the ACTION_BUTTONS entry that triggers a GPS
# refresh; the poller gates both the discovery button and the command on it (and on the
# location opt-out), so it's shared between the control layer and the MQTT discovery layer.
REFRESH_LOCATION_EP = "actions/refresh-location"
NUMBERS = {
    "r5_soc_min_target": ("SOC Min Target", "mdi:battery-arrow-down", 15, 45, 5),
    "r5_soc_max_target": ("SOC Max Target", "mdi:battery-arrow-up", 55, 100, 5),
}
NUMBER_ROLES = {"r5_soc_min_target": "min", "r5_soc_max_target": "target"}

# Sensor object_ids a previous version published but no longer ships. Their retained
# discovery config is cleared on startup so upgraded installs don't keep a dead entity.
# soc_*_target moved from SENSORS to NUMBERS, so clear their old sensor configs.
RETIRED_SENSORS = ["r5_soc_max_target", "r5_soc_min_target"]

# Published but disabled in the entity registry by default — mapping artifacts with no
# user-meaningful state. drive_side is just RHD/LHD derived from locale (used internally for
# heated-seat mapping); it adds noise to the entity list. Users who want it can re-enable it.
DEFAULT_DISABLED_SENSORS = {"r5_drive_side"}
