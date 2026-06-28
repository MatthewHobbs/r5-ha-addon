"""Entity catalog for the Renault 5 — the declarative per-model tables (sensors, binary
sensors, icons, optional endpoints, control buttons, charge-limit numbers). Object_ids are
prefixed "r5_" (following the Topolino naming, minus the legacy _api/_mi suffixes); the
discovery value_template strips that prefix. Kept separate from main.py to match the Alpine
A290 add-on's structure (lockstep)."""

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

# suffix (renault_5/cmd/<key>) -> (object_id, node-segment, name, icon, action endpoint).
# Published only when supports_endpoint() is true, so a forbidden control is never shown.
ACTION_BUTTONS = {
    "charge_start": ("r5_charge_start", "charge_start", "Start Charging", "mdi:ev-station", "actions/charge-start"),
    "lights":       ("r5_flash_lights", "flash_lights", "Flash Lights", "mdi:car-light-high", "actions/lights-start"),
    "horn":         ("r5_sound_horn", "sound_horn", "Sound Horn", "mdi:bullhorn", "actions/horn-start"),
    "hvac_start":   ("r5_start_air_conditioner", "start_air_conditioner", "Start Air Conditioner", "mdi:air-conditioner", "actions/hvac-start"),
    "hvac_stop":    ("r5_stop_air_conditioner", "stop_air_conditioner", "Stop Air Conditioner", "mdi:fan-off", "actions/hvac-stop"),
    "refresh_location": ("r5_refresh_location", "refresh_location", "Refresh Location", "mdi:crosshairs-gps", "actions/refresh-location"),
}

# Writable charge-limit controls. State comes from the poll's soc-levels read (data key =
# object_id without the r5_ prefix); a slider move writes via set_battery_soc(). Gated on
# SOC_ENDPOINT, so a model that rejects the write never ships the control.
# object_id -> (name, icon, role, min, max, step); role ("min"/"target") selects the arg.
SOC_ENDPOINT = "soc-levels"
# Authoritative recent-charge-sessions endpoint (renault_api get_charges). Supported by the
# R5 (R5E1VE) and A290 (A5E1AE) in _VEHICLE_ENDPOINTS; probed at startup so a model that
# forbids it falls back to the live-inferred Last Charge instead. (charge-history is forbidden
# on both, so only charges is used.)
CHARGES_ENDPOINT = "charges"
NUMBERS = {
    "r5_soc_min_target": ("SOC Min Target", "mdi:battery-arrow-down", "min",    15, 45,  5),
    "r5_soc_max_target": ("SOC Max Target", "mdi:battery-arrow-up",   "target", 55, 100, 5),
}

# Sensor object_ids a previous version published but no longer ships. Their retained
# discovery config is cleared on startup so upgraded installs don't keep a dead entity.
# soc_*_target moved from SENSORS to NUMBERS, so clear their old sensor configs.
RETIRED_SENSORS = ["r5_soc_max_target", "r5_soc_min_target"]

# Published but disabled in the entity registry by default — mapping artifacts with no
# user-meaningful state. drive_side is just RHD/LHD derived from locale (used internally for
# heated-seat mapping); it adds noise to the entity list. Users who want it can re-enable it.
DEFAULT_DISABLED_SENSORS = {"r5_drive_side"}
