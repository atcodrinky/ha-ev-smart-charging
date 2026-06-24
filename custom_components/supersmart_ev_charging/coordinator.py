"""Coordinator for SuperSmart EV Charging – core smart charging logic."""
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
    DEFAULT_PV_START_CURRENT_A,
    DEFAULT_PV_STOP_CURRENT_A,
    DEFAULT_PV_STOP_CONFIRM_CYCLES,
    DEFAULT_PV_START_CONFIRM_CYCLES,
    CHARGING_MODE_IDLE,
    CHARGING_MODE_PV_SURPLUS,
    CHARGING_MODE_NIGHT,
    CHARGING_MODE_FORCE,
    CHARGING_MODE_MASTER_STOP,
)

_LOGGER = logging.getLogger(__name__)


class SuperSmartEvChargingCoordinator(DataUpdateCoordinator):
    """
    SuperSmart EV Charging coordinator.

    Manages all smart charging logic:
    - PV surplus charging with hysteresis (start/stop thresholds + confirmation cycles)
      to avoid on/off cycling caused by passing clouds or morning ramp-up
    - Off-peak / night tariff charging
    - Contract power load balancing
    - Force charge and master stop overrides
    - Generic MQTT wallbox control (topics and payloads fully configurable)
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self.entry = entry
        d = entry.data

        # ── Feature flags
        self._mqtt_enabled: bool   = d.get(CONF_MQTT_ENABLED, True)
        self._tariff_enabled: bool = d.get(CONF_TARIFF_ENABLED, True)

        # ── MQTT topics & payloads
        self._topic_authorize   = d.get(CONF_MQTT_TOPIC_AUTHORIZE,   "wallbox/command/authorize")
        self._topic_revoke      = d.get(CONF_MQTT_TOPIC_REVOKE,      "wallbox/command/revoke")
        self._topic_set_current = d.get(CONF_MQTT_TOPIC_SET_CURRENT, "wallbox/command/set_current_limit")
        self._topic_set_mode    = d.get(CONF_MQTT_TOPIC_SET_MODE,    "wallbox/command/set_mode")
        self._payload_solar     = d.get(CONF_MQTT_PAYLOAD_MODE_SOLAR,   "1")
        self._payload_normal    = d.get(CONF_MQTT_PAYLOAD_MODE_NORMAL,  "2")
        self._payload_pause     = d.get(CONF_MQTT_PAYLOAD_MODE_PAUSE,   "3")

        # ── Entity IDs
        self._soc_entity             = d.get(CONF_VEHICLE_SOC_ENTITY, "")
        self._charge_limit_entity    = d.get(CONF_VEHICLE_CHARGE_LIMIT_ENTITY, "")
        self._connected_entity       = d.get(CONF_VEHICLE_CONNECTED_ENTITY, "")
        self._grid_entity            = d.get(CONF_GRID_POWER_ENTITY, "")
        self._pv_entity              = d.get(CONF_PV_POWER_ENTITY, "")
        self._wallbox_power_entity   = d.get(CONF_WALLBOX_POWER_ENTITY, "")
        self._wallbox_voltage_entity = d.get(CONF_WALLBOX_VOLTAGE_ENTITY, "")
        self._tariff_entity          = d.get(CONF_TARIFF_ENTITY, "")
        self._tariff_offpeak         = d.get(CONF_TARIFF_OFFPEAK_VALUE, DEFAULT_TARIFF_OFFPEAK_VALUE)

        # ── Power / capacity
        self._contract_power_w: float    = d.get(CONF_CONTRACT_POWER_W,     DEFAULT_CONTRACT_POWER_W)
        self._battery_capacity_kwh: float = d.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)

        # ── Controllable state (exposed as HA entities)
        # SOC targets seeded from config entry so first run uses user-chosen values
        self.master_stop: bool             = False
        self.force_charge: bool            = False
        self.solar_controller_active: bool = False
        self.night_charging_enabled: bool  = True
        self.user_soc_target: float        = float(d.get("initial_user_soc_target",    DEFAULT_USER_SOC_TARGET))
        self.vehicle_soc_target: float     = float(d.get("initial_vehicle_soc_target", DEFAULT_VEHICLE_SOC_TARGET))
        self.allowed_import_w: float       = DEFAULT_ALLOWED_IMPORT_W
        self.night_power_limit_w: float    = DEFAULT_NIGHT_POWER_LIMIT_W

        # ── PV hysteresis counters
        # Counts consecutive cycles where surplus is BELOW stop threshold (while charging)
        self._pv_below_stop_cycles: int  = 0
        # Counts consecutive cycles where surplus is ABOVE start threshold (while idle)
        self._pv_above_start_cycles: int = 0

        # ── Derived / computed state
        self.charging_mode: str              = CHARGING_MODE_IDLE
        self.pv_surplus_w: float             = 0.0
        self.target_soc_active: float        = DEFAULT_VEHICLE_SOC_TARGET
        self.wallbox_current_target_a: float = 0.0
        self.last_limit_sent_a: float        = 0.0
        self.last_authorization_ts: datetime | None = None
        self.last_revoke_ts: datetime | None        = None

    # ── DataUpdateCoordinator ──────────────────────────────────────────────────
    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch sensor values and compute all derived state."""
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

            # PV surplus: negative grid = exporting to grid
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
            soc           = data["vehicle_soc"]
            remaining_kwh = max(0.0, (self.target_soc_active - soc) / 100.0 * self._battery_capacity_kwh)
            wallbox_kw    = data["wallbox_power_w"] / 1000.0
            remaining_min = (remaining_kwh / wallbox_kw * 60) if wallbox_kw > 0.1 else None
            data["remaining_minutes"] = remaining_min
            data["charge_end_time"]   = (
                datetime.now() + timedelta(minutes=remaining_min)
                if remaining_min is not None else None
            )

            # Theoretical current from available PV surplus
            voltage = data["wallbox_voltage_v"]
            self.wallbox_current_target_a = min(
                DEFAULT_MAX_CHARGE_CURRENT_A,
                max(0.0, (self.pv_surplus_w + self.allowed_import_w) / voltage),
            )
            data["wallbox_current_target_a"] = self.wallbox_current_target_a

            # Expose counters for diagnostics
            data["pv_below_stop_cycles"]  = self._pv_below_stop_cycles
            data["pv_above_start_cycles"] = self._pv_above_start_cycles

            data["charging_mode"]           = self.charging_mode
            data["master_stop"]             = self.master_stop
            data["force_charge"]            = self.force_charge
            data["solar_controller_active"] = self.solar_controller_active

        except Exception as err:
            raise UpdateFailed(f"SuperSmart EV Charging – error fetching data: {err}") from err

        return data

    # ── Main charging decision loop ────────────────────────────────────────────
    async def async_update_charging_logic(self, _now: datetime | None = None) -> None:
        """
        Evaluate charging conditions every 30 s and send MQTT commands.

        PV surplus uses a two-threshold hysteresis with confirmation cycles:
        - START: surplus_a >= DEFAULT_PV_START_CURRENT_A for N consecutive cycles
        - STOP:  surplus_a <  DEFAULT_PV_STOP_CURRENT_A  for M consecutive cycles
        This eliminates on/off cycling from passing clouds or morning ramp-up.
        """
        await self.async_refresh()
        data = self.data
        if not data:
            return

        vehicle_connected = data.get("vehicle_connected", False)
        vehicle_soc       = data.get("vehicle_soc", 0.0)
        is_offpeak        = data.get("is_offpeak", False)

        # ── 1. MASTER STOP – highest priority ─────────────────────────────────
        if self.master_stop:
            if self.charging_mode != CHARGING_MODE_MASTER_STOP:
                _LOGGER.info("[SuperSmart] Master Stop active – revoking authorization")
                await self._revoke()
                self.charging_mode = CHARGING_MODE_MASTER_STOP
                self._reset_pv_counters()
                self.async_update_listeners()
            return

        # ── Vehicle disconnected ───────────────────────────────────────────────
        if not vehicle_connected:
            if self.charging_mode != CHARGING_MODE_IDLE:
                self.charging_mode = CHARGING_MODE_IDLE
                self.solar_controller_active = False
                self._reset_pv_counters()
                self.async_update_listeners()
            return

        # ── 2. FORCE CHARGE ───────────────────────────────────────────────────
        if self.force_charge:
            self._reset_pv_counters()
            if vehicle_soc >= self.vehicle_soc_target:
                _LOGGER.info("[SuperSmart] Force charge complete (SOC %.0f%%) – stopping", vehicle_soc)
                self.force_charge = False
                await self._revoke()
                self.charging_mode = CHARGING_MODE_IDLE
            else:
                if self.charging_mode != CHARGING_MODE_FORCE:
                    _LOGGER.info("[SuperSmart] Force charge activated – starting")
                    await self._set_mode(self._payload_normal)
                    await self._send_limit(await self._load_balanced_current(data))
                    await self._authorize()
                    self.charging_mode = CHARGING_MODE_FORCE
                else:
                    await self._send_limit_if_changed(await self._load_balanced_current(data))
            self.async_update_listeners()
            return

        # ── 3. NIGHT / OFF-PEAK TARIFF (with smart PV transition) ─────────────
        if self._tariff_enabled and is_offpeak and self.night_charging_enabled:
            voltage   = data.get("wallbox_voltage_v", 230.0)
            surplus_a = self._w_to_a(self.pv_surplus_w + self.allowed_import_w, voltage)

            if vehicle_soc >= self.user_soc_target:
                # SOC target reached → stop regardless of mode
                if self.charging_mode in (CHARGING_MODE_NIGHT, CHARGING_MODE_PV_SURPLUS):
                    _LOGGER.info(
                        "[SuperSmart] F3: SOC %.0f%% reached user target – stopping",
                        vehicle_soc,
                    )
                    await self._revoke()
                    self.charging_mode = CHARGING_MODE_IDLE
                    self.solar_controller_active = False
                    self._reset_pv_counters()
                self.async_update_listeners()
                return

            # SOC below user target – decide NIGHT vs PV_SURPLUS.
            # Mirrors original automation skip_in_f3 logic:
            # if F3 + soc < limite + surplus < 7A → use grid (night)
            # if F3 + soc < limite + surplus >= 7A → prefer solar
            pv_sufficient = surplus_a >= DEFAULT_PV_START_CURRENT_A  # 7A

            if pv_sufficient:
                # ── Enough PV even in F3 → use/switch to solar mode ───────────
                if self.charging_mode == CHARGING_MODE_NIGHT:
                    # Transition NIGHT → PV_SURPLUS
                    _LOGGER.info(
                        "[SuperSmart] F3: PV surplus %.1fA >= %.0fA – switching NIGHT → PV_SURPLUS",
                        surplus_a, DEFAULT_PV_START_CURRENT_A,
                    )
                    await self._revoke()
                    self._reset_pv_counters()
                    self.charging_mode = CHARGING_MODE_IDLE
                    self.solar_controller_active = False
                    # Will restart as PV_SURPLUS on next cycle
                    self.async_update_listeners()
                    return

                if self.charging_mode == CHARGING_MODE_PV_SURPLUS:
                    # Already in solar mode – apply stop hysteresis
                    if surplus_a < DEFAULT_PV_STOP_CURRENT_A:
                        self._pv_below_stop_cycles += 1
                        self._pv_above_start_cycles = 0
                        _LOGGER.debug(
                            "[SuperSmart] F3/PV: surplus %.1fA below stop – cycle %d/%d",
                            surplus_a, self._pv_below_stop_cycles, DEFAULT_PV_STOP_CONFIRM_CYCLES,
                        )
                        if self._pv_below_stop_cycles >= DEFAULT_PV_STOP_CONFIRM_CYCLES:
                            _LOGGER.info(
                                "[SuperSmart] F3/PV: surplus confirmed low – reverting to NIGHT"
                            )
                            await self._set_mode(self._payload_pause)
                            await self._revoke()
                            self.charging_mode = CHARGING_MODE_IDLE
                            self.solar_controller_active = False
                            self._reset_pv_counters()
                    else:
                        self._pv_below_stop_cycles = 0
                        capped_a = min(surplus_a, await self._load_balanced_current(data))
                        await self._send_limit_if_changed(capped_a)
                else:
                    # IDLE → start solar charging in F3
                    self._pv_above_start_cycles += 1
                    if self._pv_above_start_cycles >= DEFAULT_PV_START_CONFIRM_CYCLES:
                        _LOGGER.info(
                            "[SuperSmart] F3: PV surplus stable – starting solar charging"
                        )
                        await self._set_mode(self._payload_solar)
                        capped_a = min(surplus_a, await self._load_balanced_current(data))
                        await self._send_limit(capped_a)
                        await self._authorize()
                        self.charging_mode = CHARGING_MODE_PV_SURPLUS
                        self.solar_controller_active = True
                        self._reset_pv_counters()

            else:
                # ── Not enough PV → use night/grid charging ────────────────────
                self._reset_pv_counters()
                if self.charging_mode == CHARGING_MODE_PV_SURPLUS:
                    # Transition PV_SURPLUS → NIGHT (surplus dropped while in F3)
                    _LOGGER.info(
                        "[SuperSmart] F3: PV surplus %.1fA < %.0fA – switching PV_SURPLUS → NIGHT",
                        surplus_a, DEFAULT_PV_START_CURRENT_A,
                    )
                    self.solar_controller_active = False
                    self.charging_mode = CHARGING_MODE_NIGHT
                    await self._set_mode(self._payload_normal)
                    night_a = min(
                        self._w_to_a(self.night_power_limit_w, voltage),
                        await self._load_balanced_current(data),
                    )
                    await self._send_limit(night_a)

                elif self.charging_mode != CHARGING_MODE_NIGHT:
                    _LOGGER.info(
                        "[SuperSmart] F3: starting night charging (target %.0f%%)",
                        self.user_soc_target,
                    )
                    await self._set_mode(self._payload_normal)
                    night_a = min(
                        self._w_to_a(self.night_power_limit_w, voltage),
                        await self._load_balanced_current(data),
                    )
                    await self._send_limit(night_a)
                    await self._authorize()
                    self.charging_mode = CHARGING_MODE_NIGHT
                    self.solar_controller_active = False

                else:
                    # Ongoing night charging – maintain load-balanced limit
                    night_a = min(
                        self._w_to_a(self.night_power_limit_w, voltage),
                        await self._load_balanced_current(data),
                    )
                    await self._send_limit_if_changed(night_a)

            self.async_update_listeners()
            return


        # ── 4. PV SURPLUS CHARGING (with hysteresis) ──────────────────────────
        voltage   = data.get("wallbox_voltage_v", 230.0)
        surplus_a = self._w_to_a(self.pv_surplus_w + self.allowed_import_w, voltage)

        if self.charging_mode == CHARGING_MODE_PV_SURPLUS:
            # ── Already charging on solar ──────────────────────────────────────
            if vehicle_soc >= self.vehicle_soc_target:
                # SOC target reached → stop immediately, no confirmation needed
                _LOGGER.info(
                    "[SuperSmart] SOC %.0f%% reached vehicle target – stopping PV charging",
                    vehicle_soc,
                )
                await self._revoke()
                self.charging_mode = CHARGING_MODE_IDLE
                self.solar_controller_active = False
                self._reset_pv_counters()

            elif surplus_a < DEFAULT_PV_STOP_CURRENT_A:
                # Surplus dropped below stop threshold – start confirmation countdown
                self._pv_below_stop_cycles += 1
                self._pv_above_start_cycles = 0
                _LOGGER.debug(
                    "[SuperSmart] PV surplus %.1fA below stop threshold %.0fA "
                    "– confirm cycle %d/%d",
                    surplus_a,
                    DEFAULT_PV_STOP_CURRENT_A,
                    self._pv_below_stop_cycles,
                    DEFAULT_PV_STOP_CONFIRM_CYCLES,
                )
                if self._pv_below_stop_cycles >= DEFAULT_PV_STOP_CONFIRM_CYCLES:
                    # Confirmed sustained drop → pause charging
                    _LOGGER.info(
                        "[SuperSmart] PV surplus confirmed low for %d cycles – pausing",
                        self._pv_below_stop_cycles,
                    )
                    await self._set_mode(self._payload_pause)
                    await self._revoke()
                    self.charging_mode = CHARGING_MODE_IDLE
                    self.solar_controller_active = False
                    self._reset_pv_counters()
                # else: keep charging, just don't update current this cycle

            else:
                # Surplus still sufficient → reset stop counter and adjust current
                self._pv_below_stop_cycles = 0
                self._pv_above_start_cycles = 0
                capped_a = min(surplus_a, await self._load_balanced_current(data))
                await self._send_limit_if_changed(capped_a)

        else:
            # ── Not currently charging on solar ───────────────────────────────
            if surplus_a >= DEFAULT_PV_START_CURRENT_A and vehicle_soc < self.vehicle_soc_target:
                # Surplus above start threshold – count consecutive cycles
                self._pv_above_start_cycles += 1
                self._pv_below_stop_cycles = 0
                _LOGGER.debug(
                    "[SuperSmart] PV surplus %.1fA above start threshold %.0fA "
                    "– confirm cycle %d/%d",
                    surplus_a,
                    DEFAULT_PV_START_CURRENT_A,
                    self._pv_above_start_cycles,
                    DEFAULT_PV_START_CONFIRM_CYCLES,
                )
                if self._pv_above_start_cycles >= DEFAULT_PV_START_CONFIRM_CYCLES:
                    # Confirmed stable surplus → start solar charging
                    _LOGGER.info(
                        "[SuperSmart] PV surplus %.0fW confirmed stable for %d cycle(s) – starting",
                        self.pv_surplus_w,
                        self._pv_above_start_cycles,
                    )
                    await self._set_mode(self._payload_solar)
                    capped_a = min(surplus_a, await self._load_balanced_current(data))
                    await self._send_limit(capped_a)
                    await self._authorize()
                    self.charging_mode = CHARGING_MODE_PV_SURPLUS
                    self.solar_controller_active = True
                    self._reset_pv_counters()
            else:
                # Surplus below start threshold or SOC already at target
                self._pv_above_start_cycles = 0
                self._pv_below_stop_cycles = 0

        self.async_update_listeners()

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _reset_pv_counters(self) -> None:
        """Reset both hysteresis counters (call on mode change or vehicle disconnect)."""
        self._pv_below_stop_cycles  = 0
        self._pv_above_start_cycles = 0

    async def _load_balanced_current(self, data: dict[str, Any]) -> float:
        """Return max charge current that keeps total load below contract limit."""
        grid_w       = data.get("grid_power_w", 0.0)
        pv_w         = data.get("pv_power_w", 0.0)
        wallbox_w    = data.get("wallbox_power_w", 0.0)
        voltage      = data.get("wallbox_voltage_v", 230.0)
        house_load_w = max(0.0, grid_w + pv_w - wallbox_w)
        available_w  = self._contract_power_w - house_load_w - DEFAULT_SAFETY_MARGIN_W
        raw_a        = self._w_to_a(available_w, voltage)
        return float(min(DEFAULT_MAX_CHARGE_CURRENT_A, max(DEFAULT_MIN_CHARGE_CURRENT_A, raw_a)))

    async def _send_limit_if_changed(self, current_a: float) -> None:
        """Send limit only if it changed by ≥0.5A to reduce MQTT traffic."""
        if abs(current_a - self.last_limit_sent_a) >= 0.5:
            await self._send_limit(current_a)

    # ── MQTT commands ──────────────────────────────────────────────────────────
    async def _authorize(self) -> None:
        if not self._mqtt_enabled:
            return
        await mqtt.async_publish(self.hass, self._topic_authorize, "1", qos=1)
        self.last_authorization_ts = datetime.now()
        _LOGGER.debug("[SuperSmart] Authorize → %s", self._topic_authorize)

    async def _revoke(self) -> None:
        if not self._mqtt_enabled:
            return
        await mqtt.async_publish(self.hass, self._topic_revoke, "1", qos=1)
        self.last_revoke_ts = datetime.now()
        _LOGGER.debug("[SuperSmart] Revoke → %s", self._topic_revoke)

    async def _send_limit(self, current_a: float) -> None:
        clamped = int(min(DEFAULT_MAX_CHARGE_CURRENT_A, max(DEFAULT_MIN_CHARGE_CURRENT_A, current_a)))
        if self._mqtt_enabled:
            await mqtt.async_publish(self.hass, self._topic_set_current, str(clamped), qos=1)
        self.last_limit_sent_a = clamped
        _LOGGER.debug("[SuperSmart] Current limit %dA → %s", clamped, self._topic_set_current)

    async def _set_mode(self, payload: str) -> None:
        if not self._mqtt_enabled or not self._topic_set_mode:
            return
        await mqtt.async_publish(self.hass, self._topic_set_mode, payload, qos=1)
        _LOGGER.debug("[SuperSmart] Mode '%s' → %s", payload, self._topic_set_mode)

    # Public wrappers used by services and switches
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
