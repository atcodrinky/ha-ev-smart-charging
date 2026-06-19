"""Coordinator for EV Smart Charging – generic vehicle/wallbox logic."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_CONTRACT_POWER_W,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_VEHICLE_SOC_ENTITY,
    CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
    CONF_VEHICLE_CONNECTED_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_WALLBOX_POWER_ENTITY,
    CONF_WALLBOX_VOLTAGE_ENTITY,
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
    DEFAULT_ALLOWED_IMPORT_W,
    DEFAULT_MIN_CHARGE_CURRENT_A,
    DEFAULT_MAX_CHARGE_CURRENT_A,
    DEFAULT_NIGHT_POWER_LIMIT_W,
    DEFAULT_USER_SOC_TARGET,
    DEFAULT_VEHICLE_SOC_TARGET,
    DEFAULT_SAFETY_MARGIN_W,
    DEFAULT_TARIFF_OFFPEAK_VALUE,
    CHARGING_MODE_IDLE,
    CHARGING_MODE_PV_SURPLUS,
    CHARGING_MODE_NIGHT,
    CHARGING_MODE_FORCE,
    CHARGING_MODE_MASTER_STOP,
)

_LOGGER = logging.getLogger(__name__)


class EvSmartChargingCoordinator(DataUpdateCoordinator):
    """
    Generic EV smart charging coordinator.

    Works with any EV + wallbox combination.
    MQTT topics and payloads are fully configurable.
    Tariff sensor is optional (any entity returning a string value).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=30))
        self.entry = entry
        d = entry.data

        # ── Feature flags
        self._mqtt_enabled: bool   = d.get(CONF_MQTT_ENABLED, True)
        self._tariff_enabled: bool = d.get(CONF_TARIFF_ENABLED, True)

        # ── MQTT topics & payloads (user-configurable)
        self._topic_authorize   = d.get(CONF_MQTT_TOPIC_AUTHORIZE,   "wallbox/command/authorize")
        self._topic_revoke      = d.get(CONF_MQTT_TOPIC_REVOKE,      "wallbox/command/revoke")
        self._topic_set_current = d.get(CONF_MQTT_TOPIC_SET_CURRENT, "wallbox/command/set_current_limit")
        self._topic_set_mode    = d.get(CONF_MQTT_TOPIC_SET_MODE,    "wallbox/command/set_mode")
        self._payload_solar     = d.get(CONF_MQTT_PAYLOAD_MODE_SOLAR,   "1")
        self._payload_normal    = d.get(CONF_MQTT_PAYLOAD_MODE_NORMAL,  "2")
        self._payload_pause     = d.get(CONF_MQTT_PAYLOAD_MODE_PAUSE,   "3")

        # ── Entity IDs (generic – no Skoda/Silla defaults)
        self._soc_entity          = d.get(CONF_VEHICLE_SOC_ENTITY, "")
        self._charge_limit_entity = d.get(CONF_VEHICLE_CHARGE_LIMIT_ENTITY, "")
        self._connected_entity    = d.get(CONF_VEHICLE_CONNECTED_ENTITY, "")
        self._grid_entity         = d.get(CONF_GRID_POWER_ENTITY, "")
        self._pv_entity           = d.get(CONF_PV_POWER_ENTITY, "")
        self._wallbox_power_entity   = d.get(CONF_WALLBOX_POWER_ENTITY, "")
        self._wallbox_voltage_entity = d.get(CONF_WALLBOX_VOLTAGE_ENTITY, "")
        self._tariff_entity       = d.get(CONF_TARIFF_ENTITY, "")
        self._tariff_offpeak      = d.get(CONF_TARIFF_OFFPEAK_VALUE, DEFAULT_TARIFF_OFFPEAK_VALUE)

        # ── Power / capacity
        self._contract_power_w: float   = d.get(CONF_CONTRACT_POWER_W,    DEFAULT_CONTRACT_POWER_W)
        self._battery_capacity_kwh: float = d.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)

        # ── Controllable state (exposed as HA entities)
        self.master_stop: bool              = False
        self.force_charge: bool             = False
        self.solar_controller_active: bool  = False
        self.night_charging_enabled: bool   = True   # can be toggled per-user
        self.user_soc_target: float         = DEFAULT_USER_SOC_TARGET
        self.vehicle_soc_target: float      = DEFAULT_VEHICLE_SOC_TARGET
        self.allowed_import_w: float        = DEFAULT_ALLOWED_IMPORT_W
        self.night_power_limit_w: float     = DEFAULT_NIGHT_POWER_LIMIT_W

        # ── Derived / computed state
        self.charging_mode: str             = CHARGING_MODE_IDLE
        self.pv_surplus_w: float            = 0.0
        self.target_soc_active: float       = DEFAULT_VEHICLE_SOC_TARGET
        self.wallbox_current_target_a: float = 0.0
        self.last_limit_sent_a: float       = 0.0
        self.last_authorization_ts: datetime | None = None
        self.last_revoke_ts: datetime | None        = None

    # ── DataUpdateCoordinator ──────────────────────────────────────────────────
    async def _async_update_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        try:
            data["vehicle_soc"]       = self._get_float(self._soc_entity)
            data["vehicle_connected"] = self._get_bool(self._connected_entity)
            data["grid_power_w"]      = self._get_float(self._grid_entity)
            data["pv_power_w"]        = self._get_float(self._pv_entity)
            data["wallbox_power_w"]   = self._get_float(self._wallbox_power_entity)
            data["wallbox_voltage_v"] = self._get_float(self._wallbox_voltage_entity, default=230.0)

            # Tariff – optional
            if self._tariff_enabled and self._tariff_entity:
                data["tariff_value"] = self._get_state(self._tariff_entity, default="")
                data["is_offpeak"]   = data["tariff_value"] == self._tariff_offpeak
            else:
                data["tariff_value"] = ""
                data["is_offpeak"]   = False

            # PV surplus: negative grid = export
            grid_w = data["grid_power_w"]
            pv_w   = data["pv_power_w"]
            self.pv_surplus_w = abs(grid_w) if grid_w < 0 else max(0.0, pv_w - grid_w)
            data["pv_surplus_w"] = self.pv_surplus_w

            # Active SOC target
            self.target_soc_active = (
                self.vehicle_soc_target
                if (self.force_charge or self.solar_controller_active)
                else self.user_soc_target
            )
            data["target_soc_active"] = self.target_soc_active

            # Charging time estimate
            soc         = data["vehicle_soc"]
            remaining_kwh = max(0.0, (self.target_soc_active - soc) / 100.0 * self._battery_capacity_kwh)
            wallbox_kw  = data["wallbox_power_w"] / 1000.0
            remaining_minutes = (remaining_kwh / wallbox_kw * 60) if wallbox_kw > 0.1 else None
            data["remaining_minutes"] = remaining_minutes
            data["charge_end_time"]   = (
                datetime.now() + timedelta(minutes=remaining_minutes)
                if remaining_minutes is not None else None
            )

            # Theoretical current from PV surplus
            voltage = data["wallbox_voltage_v"]
            self.wallbox_current_target_a = min(
                DEFAULT_MAX_CHARGE_CURRENT_A,
                max(0.0, (self.pv_surplus_w + self.allowed_import_w) / voltage)
            )
            data["wallbox_current_target_a"] = self.wallbox_current_target_a

            data["charging_mode"]          = self.charging_mode
            data["master_stop"]            = self.master_stop
            data["force_charge"]           = self.force_charge
            data["solar_controller_active"] = self.solar_controller_active

        except Exception as err:
            raise UpdateFailed(f"Error fetching EV data: {err}") from err
        return data

    # ── Main charging decision logic ───────────────────────────────────────────
    async def async_update_charging_logic(self, _now: datetime | None = None) -> None:
        """Evaluate conditions every 30 s and act accordingly."""
        await self.async_refresh()
        data = self.data
        if not data:
            return

        vehicle_connected = data.get("vehicle_connected", False)
        vehicle_soc       = data.get("vehicle_soc", 0.0)
        is_offpeak        = data.get("is_offpeak", False)

        # ── 1. MASTER STOP
        if self.master_stop:
            if self.charging_mode != CHARGING_MODE_MASTER_STOP:
                _LOGGER.info("Master Stop active – revoking authorization")
                await self._revoke()
                self.charging_mode = CHARGING_MODE_MASTER_STOP
                self.async_update_listeners()
            return

        if not vehicle_connected:
            if self.charging_mode != CHARGING_MODE_IDLE:
                self.charging_mode = CHARGING_MODE_IDLE
                self.solar_controller_active = False
                self.async_update_listeners()
            return

        # ── 2. FORCE CHARGE
        if self.force_charge:
            if vehicle_soc >= self.vehicle_soc_target:
                _LOGGER.info("Force charge complete (SOC %.0f%%) – stopping", vehicle_soc)
                self.force_charge = False
                await self._revoke()
                self.charging_mode = CHARGING_MODE_IDLE
            else:
                if self.charging_mode != CHARGING_MODE_FORCE:
                    _LOGGER.info("Force charge – starting normal mode")
                    await self._set_mode(self._payload_normal)
                    await self._send_limit(await self._load_balanced_current(data))
                    await self._authorize()
                    self.charging_mode = CHARGING_MODE_FORCE
                else:
                    await self._send_limit_if_changed(await self._load_balanced_current(data))
            self.async_update_listeners()
            return

        # ── 3. NIGHT / OFF-PEAK TARIFF
        if self._tariff_enabled and is_offpeak and self.night_charging_enabled:
            if vehicle_soc < self.user_soc_target:
                if self.charging_mode != CHARGING_MODE_NIGHT:
                    _LOGGER.info("Off-peak tariff – starting night charging (target %.0f%%)", self.user_soc_target)
                    await self._set_mode(self._payload_normal)
                    night_a = min(
                        self._w_to_a(self.night_power_limit_w, data.get("wallbox_voltage_v", 230.0)),
                        await self._load_balanced_current(data),
                    )
                    await self._send_limit(night_a)
                    await self._authorize()
                    self.charging_mode = CHARGING_MODE_NIGHT
                    self.solar_controller_active = False
                else:
                    night_a = min(
                        self._w_to_a(self.night_power_limit_w, data.get("wallbox_voltage_v", 230.0)),
                        await self._load_balanced_current(data),
                    )
                    await self._send_limit_if_changed(night_a)
            else:
                if self.charging_mode == CHARGING_MODE_NIGHT:
                    _LOGGER.info("Night charging complete (SOC %.0f%%) – stopping", vehicle_soc)
                    await self._revoke()
                    self.charging_mode = CHARGING_MODE_IDLE
            self.async_update_listeners()
            return

        # ── 4. PV SURPLUS
        voltage   = data.get("wallbox_voltage_v", 230.0)
        surplus_a = self._w_to_a(self.pv_surplus_w + self.allowed_import_w, voltage)

        if surplus_a >= DEFAULT_MIN_CHARGE_CURRENT_A:
            if vehicle_soc < self.vehicle_soc_target:
                if self.charging_mode != CHARGING_MODE_PV_SURPLUS:
                    _LOGGER.info("PV surplus %.0fW – starting solar charging", self.pv_surplus_w)
                    await self._set_mode(self._payload_solar)
                    capped_a = min(surplus_a, await self._load_balanced_current(data))
                    await self._send_limit(capped_a)
                    await self._authorize()
                    self.charging_mode = CHARGING_MODE_PV_SURPLUS
                    self.solar_controller_active = True
                else:
                    capped_a = min(surplus_a, await self._load_balanced_current(data))
                    await self._send_limit_if_changed(capped_a)
            else:
                if self.charging_mode == CHARGING_MODE_PV_SURPLUS:
                    _LOGGER.info("Vehicle SOC %.0f%% reached target – stopping PV charging", vehicle_soc)
                    await self._revoke()
                    self.charging_mode = CHARGING_MODE_IDLE
                    self.solar_controller_active = False
        else:
            if self.charging_mode == CHARGING_MODE_PV_SURPLUS:
                _LOGGER.info("PV surplus dropped below minimum – pausing")
                await self._set_mode(self._payload_pause)
                await self._revoke()
                self.charging_mode = CHARGING_MODE_IDLE
                self.solar_controller_active = False

        self.async_update_listeners()

    # ── Load balancing ─────────────────────────────────────────────────────────
    async def _load_balanced_current(self, data: dict[str, Any]) -> float:
        """Max charge current that keeps total load below contract limit."""
        grid_w         = data.get("grid_power_w", 0.0)
        pv_w           = data.get("pv_power_w", 0.0)
        wallbox_w      = data.get("wallbox_power_w", 0.0)
        voltage        = data.get("wallbox_voltage_v", 230.0)
        house_load_w   = max(0.0, grid_w + pv_w - wallbox_w)
        available_w    = self._contract_power_w - house_load_w - DEFAULT_SAFETY_MARGIN_W
        raw_a          = self._w_to_a(available_w, voltage)
        return float(min(DEFAULT_MAX_CHARGE_CURRENT_A, max(DEFAULT_MIN_CHARGE_CURRENT_A, raw_a)))

    async def _send_limit_if_changed(self, current_a: float) -> None:
        if abs(current_a - self.last_limit_sent_a) >= 0.5:
            await self._send_limit(current_a)

    # ── MQTT helpers ───────────────────────────────────────────────────────────
    async def _authorize(self) -> None:
        if not self._mqtt_enabled:
            return
        await mqtt.async_publish(self.hass, self._topic_authorize, "1", qos=1)
        self.last_authorization_ts = datetime.now()
        _LOGGER.debug("Authorize → %s", self._topic_authorize)

    async def _revoke(self) -> None:
        if not self._mqtt_enabled:
            return
        await mqtt.async_publish(self.hass, self._topic_revoke, "1", qos=1)
        self.last_revoke_ts = datetime.now()
        _LOGGER.debug("Revoke → %s", self._topic_revoke)

    async def _send_limit(self, current_a: float) -> None:
        clamped = int(min(DEFAULT_MAX_CHARGE_CURRENT_A, max(DEFAULT_MIN_CHARGE_CURRENT_A, current_a)))
        if self._mqtt_enabled:
            await mqtt.async_publish(self.hass, self._topic_set_current, str(clamped), qos=1)
        self.last_limit_sent_a = clamped
        _LOGGER.debug("Current limit %dA → %s", clamped, self._topic_set_current)

    async def _set_mode(self, payload: str) -> None:
        if not self._mqtt_enabled or not self._topic_set_mode:
            return
        await mqtt.async_publish(self.hass, self._topic_set_mode, payload, qos=1)
        _LOGGER.debug("Mode '%s' → %s", payload, self._topic_set_mode)

    # Public wrappers used by services / switches
    async def authorize_charging(self) -> None:
        await self._authorize()

    async def revoke_charging(self) -> None:
        await self._revoke()

    async def set_current_limit(self, current_a: float) -> None:
        await self._send_limit(current_a)

    # ── State helpers ──────────────────────────────────────────────────────────
    def _get_state(self, entity_id: str, default: str = "unknown") -> str:
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        return state.state if state else default

    def _get_float(self, entity_id: str, default: float = 0.0) -> float:
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable", ""):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _get_bool(self, entity_id: str, default: bool = False) -> bool:
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        return state.state in ("on", "true", "connected", "yes", "1") if state else default

    @staticmethod
    def _w_to_a(watts: float, voltage: float) -> float:
        return (watts / voltage) if voltage > 0 else 0.0
