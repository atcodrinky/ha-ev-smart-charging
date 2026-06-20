"""SuperSmart EV Charging – generic Home Assistant integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, DEFAULT_MIN_CHARGE_CURRENT_A
from .coordinator import SuperSmartEvChargingCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SuperSmart EV Charging from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = SuperSmartEvChargingCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── Services
    async def svc_authorize(call: ServiceCall) -> None:
        await coordinator.authorize_charging()

    async def svc_revoke(call: ServiceCall) -> None:
        await coordinator.revoke_charging()

    async def svc_set_limit(call: ServiceCall) -> None:
        await coordinator.set_current_limit(call.data.get("current_a", DEFAULT_MIN_CHARGE_CURRENT_A))

    hass.services.async_register(DOMAIN, "authorize_charging", svc_authorize)
    hass.services.async_register(DOMAIN, "revoke_charging",    svc_revoke)
    hass.services.async_register(DOMAIN, "set_charge_limit",   svc_set_limit)

    # ── Background charging loop (every 30 s)
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            coordinator.async_update_charging_logic,
            timedelta(seconds=30),
        )
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
