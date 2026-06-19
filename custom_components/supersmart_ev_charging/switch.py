"""Switch platform for SuperSmart EV Charging."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SWITCH_MASTER_STOP,
    SWITCH_FORCE_CHARGE,
    SWITCH_SOLAR_CONTROLLER,
    SWITCH_NIGHT_CHARGING,
)
from .coordinator import EvSmartChargingCoordinator

_LOGGER = logging.getLogger(__name__)

_DEVICE_INFO = lambda entry_id: {
    "identifiers": {(DOMAIN, entry_id)},
    "name": "EV Smart Charging",
    "manufacturer": "ev_smart_charging",
    "model": "Generic EV Energy Manager",
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: EvSmartChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        MasterStopSwitch(coordinator, entry),
        ForceChargeSwitch(coordinator, entry),
        SolarControllerSwitch(coordinator, entry),
        NightChargingSwitch(coordinator, entry),
    ])


class _Base(CoordinatorEntity[EvSmartChargingCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EvSmartChargingCoordinator, entry: ConfigEntry, suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id   = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = _DEVICE_INFO(entry.entry_id)


class MasterStopSwitch(_Base):
    _attr_name = "Master Stop"
    _attr_icon = "mdi:stop-circle"

    def __init__(self, c, e):
        super().__init__(c, e, SWITCH_MASTER_STOP)

    @property
    def is_on(self) -> bool:
        return self.coordinator.master_stop

    async def async_turn_on(self, **kwargs: Any) -> None:
        _LOGGER.warning("Master Stop ENABLED – all charging blocked")
        self.coordinator.master_stop = True
        await self.coordinator.async_update_charging_logic()

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.info("Master Stop DISABLED")
        self.coordinator.master_stop = False
        self.coordinator.async_update_listeners()


class ForceChargeSwitch(_Base):
    _attr_name = "Force Charge"
    _attr_icon = "mdi:flash"

    def __init__(self, c, e):
        super().__init__(c, e, SWITCH_FORCE_CHARGE)

    @property
    def is_on(self) -> bool:
        return self.coordinator.force_charge

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.coordinator.master_stop:
            _LOGGER.warning("Cannot enable Force Charge: Master Stop is active")
            return
        self.coordinator.force_charge = True
        await self.coordinator.async_update_charging_logic()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.force_charge = False
        await self.coordinator.revoke_charging()
        self.coordinator.async_update_listeners()


class SolarControllerSwitch(_Base):
    _attr_name = "Solar Controller Active"
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, c, e):
        super().__init__(c, e, SWITCH_SOLAR_CONTROLLER)

    @property
    def is_on(self) -> bool:
        return self.coordinator.solar_controller_active

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.solar_controller_active = True
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.solar_controller_active = False
        self.coordinator.async_update_listeners()


class NightChargingSwitch(_Base):
    """Enable/disable off-peak tariff charging independently."""

    _attr_name = "Night / Off-Peak Charging"
    _attr_icon = "mdi:weather-night"

    def __init__(self, c, e):
        super().__init__(c, e, SWITCH_NIGHT_CHARGING)

    @property
    def is_on(self) -> bool:
        return self.coordinator.night_charging_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.night_charging_enabled = True
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.night_charging_enabled = False
        self.coordinator.async_update_listeners()
