"""Select platform for EV Smart Charging."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CHARGING_MODES,
    CHARGING_MODE_IDLE,
    CHARGING_MODE_FORCE,
    CHARGING_MODE_MASTER_STOP,
)
from .coordinator import EvSmartChargingCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: EvSmartChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ChargingModeSelect(coordinator, entry)])


class ChargingModeSelect(CoordinatorEntity[EvSmartChargingCoordinator], SelectEntity):
    _attr_has_entity_name = True
    _attr_name    = "Charging Mode"
    _attr_options = CHARGING_MODES
    _attr_icon    = "mdi:ev-plug-type2"

    def __init__(self, coordinator: EvSmartChargingCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id   = f"{entry.entry_id}_charging_mode_select"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Smart Charging",
            "manufacturer": "ev_smart_charging",
            "model": "Generic EV Energy Manager",
        }

    @property
    def current_option(self) -> str:
        return self.coordinator.charging_mode

    async def async_select_option(self, option: str) -> None:
        if option == CHARGING_MODE_MASTER_STOP:
            self.coordinator.master_stop = True
            self.coordinator.force_charge = False
        elif option == CHARGING_MODE_FORCE:
            if self.coordinator.master_stop:
                _LOGGER.warning("Cannot set Force Charge: Master Stop is active")
                return
            self.coordinator.force_charge = True
        elif option == CHARGING_MODE_IDLE:
            self.coordinator.force_charge = False
            self.coordinator.master_stop  = False
            await self.coordinator.revoke_charging()
        await self.coordinator.async_update_charging_logic()
