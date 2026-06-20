"""Config flow for SuperSmart EV Charging – generic, wallbox/vehicle agnostic."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_CONTRACT_POWER_W,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_VEHICLE_SOC_ENTITY,
    CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
    CONF_VEHICLE_CONNECTED_ENTITY,
    CONF_WALLBOX_STATE_ENTITY,
    CONF_WALLBOX_POWER_ENTITY,
    CONF_WALLBOX_VOLTAGE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_TARIFF_ENTITY,
    CONF_TARIFF_OFFPEAK_VALUE,
    CONF_TARIFF_ENABLED,
    CONF_MQTT_ENABLED,
    CONF_MQTT_TOPIC_AUTHORIZE,
    CONF_MQTT_TOPIC_REVOKE,
    CONF_MQTT_TOPIC_SET_CURRENT,
    CONF_MQTT_TOPIC_SET_MODE,
    CONF_MQTT_PAYLOAD_MODE_SOLAR,
    CONF_MQTT_PAYLOAD_MODE_NORMAL,
    CONF_MQTT_PAYLOAD_MODE_PAUSE,
    DEFAULT_CONTRACT_POWER_W,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_MQTT_TOPIC_AUTHORIZE,
    DEFAULT_MQTT_TOPIC_REVOKE,
    DEFAULT_MQTT_TOPIC_SET_CURRENT,
    DEFAULT_MQTT_TOPIC_SET_MODE,
    DEFAULT_MQTT_PAYLOAD_MODE_SOLAR,
    DEFAULT_MQTT_PAYLOAD_MODE_NORMAL,
    DEFAULT_MQTT_PAYLOAD_MODE_PAUSE,
    DEFAULT_TARIFF_OFFPEAK_VALUE,
)

_LOGGER = logging.getLogger(__name__)


class SuperSmartEvChargingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """3-step config flow: power → entities → mqtt."""

    VERSION = 2

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    # ── Step 1: general settings ───────────────────────────────────────────────
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_entities()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_CONTRACT_POWER_W, default=DEFAULT_CONTRACT_POWER_W): vol.Coerce(int),
                vol.Required(CONF_BATTERY_CAPACITY_KWH, default=DEFAULT_BATTERY_CAPACITY_KWH): vol.Coerce(float),
                vol.Required(CONF_TARIFF_ENABLED, default=True): bool,
                vol.Required(CONF_MQTT_ENABLED, default=True): bool,
            }),
        )

    # ── Step 2: entity selection ───────────────────────────────────────────────
    async def async_step_entities(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            if self._data.get(CONF_MQTT_ENABLED):
                return await self.async_step_mqtt()
            return self._create_entry()

        schema_fields: dict = {
            vol.Required(CONF_VEHICLE_SOC_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_VEHICLE_CONNECTED_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Optional(CONF_VEHICLE_CHARGE_LIMIT_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="number")
            ),
            vol.Required(CONF_GRID_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_PV_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_WALLBOX_STATE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_WALLBOX_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_WALLBOX_VOLTAGE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
        }

        if self._data.get(CONF_TARIFF_ENABLED):
            schema_fields[vol.Optional(CONF_TARIFF_ENTITY)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            )
            schema_fields[vol.Optional(CONF_TARIFF_OFFPEAK_VALUE, default=DEFAULT_TARIFF_OFFPEAK_VALUE)] = str

        return self.async_show_form(
            step_id="entities",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    # ── Step 3: MQTT topics & payloads ────────────────────────────────────────
    async def async_step_mqtt(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self._create_entry()

        return self.async_show_form(
            step_id="mqtt",
            data_schema=vol.Schema({
                vol.Required(CONF_MQTT_TOPIC_AUTHORIZE,   default=DEFAULT_MQTT_TOPIC_AUTHORIZE):   str,
                vol.Required(CONF_MQTT_TOPIC_REVOKE,      default=DEFAULT_MQTT_TOPIC_REVOKE):      str,
                vol.Required(CONF_MQTT_TOPIC_SET_CURRENT, default=DEFAULT_MQTT_TOPIC_SET_CURRENT): str,
                vol.Optional(CONF_MQTT_TOPIC_SET_MODE,    default=DEFAULT_MQTT_TOPIC_SET_MODE):    str,
                vol.Optional(CONF_MQTT_PAYLOAD_MODE_SOLAR,   default=DEFAULT_MQTT_PAYLOAD_MODE_SOLAR):   str,
                vol.Optional(CONF_MQTT_PAYLOAD_MODE_NORMAL,  default=DEFAULT_MQTT_PAYLOAD_MODE_NORMAL):  str,
                vol.Optional(CONF_MQTT_PAYLOAD_MODE_PAUSE,   default=DEFAULT_MQTT_PAYLOAD_MODE_PAUSE):   str,
            }),
        )

    def _create_entry(self) -> FlowResult:
        return self.async_create_entry(title="EV Smart Charging", data=self._data)

    # ── Options flow ───────────────────────────────────────────────────────────
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> SuperSmartEvChargingOptionsFlow:
        return SuperSmartEvChargingOptionsFlow(config_entry)


class SuperSmartEvChargingOptionsFlow(config_entries.OptionsFlow):
    """Allow editing key parameters without re-running the full flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        d = self.config_entry.data
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_CONTRACT_POWER_W,    default=d.get(CONF_CONTRACT_POWER_W,    DEFAULT_CONTRACT_POWER_W)):    vol.Coerce(int),
                vol.Required(CONF_BATTERY_CAPACITY_KWH, default=d.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)): vol.Coerce(float),
                vol.Required(CONF_TARIFF_ENABLED,      default=d.get(CONF_TARIFF_ENABLED,      True)):                        bool,
                vol.Required(CONF_MQTT_ENABLED,        default=d.get(CONF_MQTT_ENABLED,        True)):                        bool,
            }),
        )
