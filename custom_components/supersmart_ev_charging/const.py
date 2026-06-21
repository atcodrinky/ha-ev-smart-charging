"""Constants for SuperSmart EV Charging integration"""

DOMAIN = "supersmart_ev_charging"

# ── Config entry keys ──────────────────────────────────────────────────────────

# Wallbox MQTT
CONF_MQTT_TOPIC_AUTHORIZE      = "mqtt_topic_authorize"
CONF_MQTT_TOPIC_REVOKE         = "mqtt_topic_revoke"
CONF_MQTT_TOPIC_SET_CURRENT    = "mqtt_topic_set_current"
CONF_MQTT_TOPIC_SET_MODE       = "mqtt_topic_set_mode"
CONF_MQTT_PAYLOAD_MODE_SOLAR   = "mqtt_payload_mode_solar"
CONF_MQTT_PAYLOAD_MODE_NORMAL  = "mqtt_payload_mode_normal"
CONF_MQTT_PAYLOAD_MODE_PAUSE   = "mqtt_payload_mode_pause"
CONF_MQTT_ENABLED              = "mqtt_enabled"

# Vehicle entities
CONF_VEHICLE_SOC_ENTITY          = "vehicle_soc_entity"
CONF_VEHICLE_CHARGE_LIMIT_ENTITY = "vehicle_charge_limit_entity"
CONF_VEHICLE_CONNECTED_ENTITY    = "vehicle_connected_entity"

# Wallbox entities
CONF_WALLBOX_STATE_ENTITY   = "wallbox_state_entity"
CONF_WALLBOX_POWER_ENTITY   = "wallbox_power_entity"
CONF_WALLBOX_VOLTAGE_ENTITY = "wallbox_voltage_entity"

# Energy entities
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_PV_POWER_ENTITY   = "pv_power_entity"

# Optional tariff entity
CONF_TARIFF_ENTITY        = "tariff_entity"
CONF_TARIFF_OFFPEAK_VALUE = "tariff_offpeak_value"
CONF_TARIFF_ENABLED       = "tariff_enabled"

# Power / capacity
CONF_CONTRACT_POWER_W     = "contract_power_w"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"

# ── Defaults ───────────────────────────────────────────────────────────────────
DEFAULT_CONTRACT_POWER_W     = 5700
DEFAULT_BATTERY_CAPACITY_KWH = 60.0
DEFAULT_ALLOWED_IMPORT_W     = 200
DEFAULT_SAFETY_MARGIN_W      = 300

DEFAULT_NIGHT_POWER_LIMIT_W  = 3000
DEFAULT_USER_SOC_TARGET      = 50
DEFAULT_VEHICLE_SOC_TARGET   = 80

# ── Charging current limits ────────────────────────────────────────────────────
DEFAULT_MIN_CHARGE_CURRENT_A = 6    # absolute hardware minimum (IEC 61851)
DEFAULT_MAX_CHARGE_CURRENT_A = 16   # absolute hardware maximum

# ── PV surplus hysteresis ──────────────────────────────────────────────────────
# Start charging only when surplus exceeds this threshold (higher = fewer false starts)
DEFAULT_PV_START_CURRENT_A = 7
# Stop charging only when surplus drops below this threshold (lower = fewer false stops)
DEFAULT_PV_STOP_CURRENT_A  = 6
# Consecutive 30s cycles below stop threshold before actually stopping
# 2 cycles = 60s, 3 cycles = 90s  → filters passing clouds
DEFAULT_PV_STOP_CONFIRM_CYCLES  = 2
# Consecutive cycles above start threshold before actually starting
DEFAULT_PV_START_CONFIRM_CYCLES = 1

# ── Default MQTT topics ────────────────────────────────────────────────────────
DEFAULT_MQTT_TOPIC_AUTHORIZE   = "wallbox/command/authorize"
DEFAULT_MQTT_TOPIC_REVOKE      = "wallbox/command/revoke"
DEFAULT_MQTT_TOPIC_SET_CURRENT = "wallbox/command/set_current_limit"
DEFAULT_MQTT_TOPIC_SET_MODE    = "wallbox/command/set_mode"
DEFAULT_MQTT_PAYLOAD_MODE_SOLAR  = "1"
DEFAULT_MQTT_PAYLOAD_MODE_NORMAL = "2"
DEFAULT_MQTT_PAYLOAD_MODE_PAUSE  = "3"
DEFAULT_TARIFF_OFFPEAK_VALUE   = "F3"

# ── Charging modes ─────────────────────────────────────────────────────────────
CHARGING_MODE_IDLE        = "idle"
CHARGING_MODE_PV_SURPLUS  = "pv_surplus"
CHARGING_MODE_NIGHT       = "night"
CHARGING_MODE_FORCE       = "force"
CHARGING_MODE_MASTER_STOP = "master_stop"

CHARGING_MODES = [
    CHARGING_MODE_IDLE,
    CHARGING_MODE_PV_SURPLUS,
    CHARGING_MODE_NIGHT,
    CHARGING_MODE_FORCE,
    CHARGING_MODE_MASTER_STOP,
]

# ── Entity unique-ID suffixes ──────────────────────────────────────────────────
SENSOR_CHARGING_MODE          = "charging_mode"
SENSOR_PV_SURPLUS             = "pv_surplus"
SENSOR_TARGET_SOC             = "target_soc"
SENSOR_TIME_REMAINING         = "time_remaining"
SENSOR_CHARGE_END_TIME        = "charge_end_time"
SENSOR_WALLBOX_CURRENT_TARGET = "wallbox_current_target"

SWITCH_MASTER_STOP      = "master_stop"
SWITCH_FORCE_CHARGE     = "force_charge"
SWITCH_SOLAR_CONTROLLER = "solar_controller"
SWITCH_NIGHT_CHARGING   = "night_charging"

NUMBER_USER_SOC_TARGET    = "user_soc_target"
NUMBER_VEHICLE_SOC_TARGET = "vehicle_soc_target"
NUMBER_CONTRACT_POWER     = "contract_power"
NUMBER_ALLOWED_IMPORT     = "allowed_import"
NUMBER_NIGHT_POWER_LIMIT  = "night_power_limit"
