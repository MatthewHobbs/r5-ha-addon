#!/usr/bin/with-contenv bashio
# shellcheck shell=bash disable=SC2155
# ---------------------------------------------------------------------------
# Renault 5 add-on entrypoint.
# Reads the add-on options + the MQTT broker details (auto-discovered from the
# Mosquitto add-on) and hands them to the Python poller as environment vars.
# ---------------------------------------------------------------------------
set -e

bashio::log.info "Starting Renault 5 add-on..."

# MQTT broker — taken from the Mosquitto add-on via the Supervisor services API.
if bashio::services.available "mqtt"; then
    export MQTT_HOST="$(bashio::services mqtt 'host')"
    export MQTT_PORT="$(bashio::services mqtt 'port')"
    export MQTT_USER="$(bashio::services mqtt 'username')"
    export MQTT_PASS="$(bashio::services mqtt 'password')"
    bashio::log.info "Using MQTT broker at ${MQTT_HOST}:${MQTT_PORT}"
else
    bashio::log.warning "No MQTT service found — install/enable the Mosquitto broker add-on."
fi

# Add-on options (the dedicated config page).
export R5_USERNAME="$(bashio::config 'username')"
export R5_PASSWORD="$(bashio::config 'password')"
export R5_ACCOUNT_ID="$(bashio::config 'account_id')"
export R5_VIN="$(bashio::config 'vin')"
export R5_LOCALE="$(bashio::config 'locale')"
export R5_POLL_INTERVAL="$(bashio::config 'poll_interval')"
export R5_BATTERY_CAPACITY_KWH="$(bashio::config 'battery_capacity_kwh')"
export R5_STALE_HOURS="$(bashio::config 'stale_hours')"
export R5_LOG_LEVEL="$(bashio::config 'log_level')"
export R5_DEBUG_DUMP="$(bashio::config 'debug_dump')"

# Dashboard auto-deploy (talks to the HA core API; SUPERVISOR_TOKEN is injected
# by the Supervisor when homeassistant_api: true).
export R5_DEPLOY_DASHBOARD="$(bashio::config 'deploy_dashboard')"
export R5_DASHBOARD_URL_PATH="$(bashio::config 'dashboard_url_path')"
export R5_REDEPLOY_DASHBOARD="$(bashio::config 'redeploy_dashboard')"
export R5_CAR_RENDER="$(bashio::config 'car_render')"

exec python3 -u /app/main.py
