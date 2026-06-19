"""Number platform for EV Smart Charging."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    NUMBER_USER_SOC_TARGET,
    NUMBER_VEHICLE_SOC_TARGET,
    NUMBER_CONTRACT_POWER,
    NUMBER_ALLOWED_IMPORT,
    NUMBER_NIGHT_POWER_LIMIT,
)
from .coordinator import EvSmartChargingCoordinator

_DEVICE_INFO = lambda entry_id: {
    "identifiers": {(DOMAIN, entry_id)},
    "name": "EV Smart Charging",
    "manufacturer": "ev_smart_charging",
    "model": "Generic EV Energy Manager",
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: EvSmartChargingCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        UserSocTargetNumber(coordinator, entry),
        VehicleSocTargetNumber(coordinator, entry),
        ContractPowerNumber(coordinator, entry),
        AllowedImportNumber(coordinator, entry),
        NightPowerLimitNumber(coordinator, entry),
    ])


class _Base(CoordinatorEntity[EvSmartChargingCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: EvSmartChargingCoordinator, entry: ConfigEntry, suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id   = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = _DEVICE_INFO(entry.entry_id)


class UserSocTargetNumber(_Base):
    _attr_name = "User SOC Target"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_icon = "mdi:battery-charging-50"

    def __init__(self, c, e):
        super().__init__(c, e, NUMBER_USER_SOC_TARGET)

    @property
    def native_value(self) -> float:
        return self.coordinator.user_soc_target

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.user_soc_target = value
        self.coordinator.async_update_listeners()


class VehicleSocTargetNumber(_Base):
    _attr_name = "Vehicle SOC Target"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 20
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_icon = "mdi:battery-charging-100"

    def __init__(self, c, e):
        super().__init__(c, e, NUMBER_VEHICLE_SOC_TARGET)

    @property
    def native_value(self) -> float:
        return self.coordinator.vehicle_soc_target

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.vehicle_soc_target = value
        self.coordinator.async_update_listeners()


class ContractPowerNumber(_Base):
    _attr_name = "Contract Power Limit"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 1500
    _attr_native_max_value = 22000
    _attr_native_step = 100
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, c, e):
        super().__init__(c, e, NUMBER_CONTRACT_POWER)

    @property
    def native_value(self) -> float:
        return self.coordinator._contract_power_w

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator._contract_power_w = value
        self.coordinator.async_update_listeners()


class AllowedImportNumber(_Base):
    _attr_name = "Allowed Grid Import"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 0
    _attr_native_max_value = 3000
    _attr_native_step = 50
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, c, e):
        super().__init__(c, e, NUMBER_ALLOWED_IMPORT)

    @property
    def native_value(self) -> float:
        return self.coordinator.allowed_import_w

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.allowed_import_w = value
        self.coordinator.async_update_listeners()


class NightPowerLimitNumber(_Base):
    _attr_name = "Night Charging Power Limit"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 1000
    _attr_native_max_value = 22000
    _attr_native_step = 100
    _attr_icon = "mdi:weather-night"

    def __init__(self, c, e):
        super().__init__(c, e, NUMBER_NIGHT_POWER_LIMIT)

    @property
    def native_value(self) -> float:
        return self.coordinator.night_power_limit_w

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.night_power_limit_w = value
        self.coordinator.async_update_listeners()
