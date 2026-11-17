"""Network Inventory: IP segments, labels and remarks for network devices."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .inventory import Inventory

PLATFORMS = [Platform.SENSOR, Platform.TEXT]

type NetworkInventoryConfigEntry = ConfigEntry[Inventory]


async def async_setup_entry(hass: HomeAssistant, entry: NetworkInventoryConfigEntry) -> bool:
    """Set up Network Inventory from a config entry."""
    inventory = Inventory(hass, entry)
    await inventory.async_setup()
    entry.runtime_data = inventory
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NetworkInventoryConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: NetworkInventoryConfigEntry) -> None:
    """Reload when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: NetworkInventoryConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing the link to a device from the UI."""
    return True
