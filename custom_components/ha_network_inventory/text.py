"""Text entity: free remark per network device."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NetworkInventoryConfigEntry
from .const import UID_REMARK, signal_device
from .inventory import DeviceRecord, Inventory


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkInventoryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    inventory = entry.runtime_data

    @callback
    def _add(records: list[DeviceRecord]) -> None:
        async_add_entities(RemarkText(inventory, rec) for rec in records)

    entry.async_on_unload(inventory.register_platform(_add))


class RemarkText(TextEntity):
    """Free-text remark (e.g. location) for a network device."""

    _attr_has_entity_name = True
    _attr_translation_key = "remark"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 255
    _attr_should_poll = False

    def __init__(self, inventory: Inventory, rec: DeviceRecord) -> None:
        self._inventory = inventory
        self._mac = rec.mac
        self._attr_unique_id = f"{rec.mac}_{UID_REMARK}"
        self._attr_device_info = DeviceInfo(connections={(CONNECTION_NETWORK_MAC, rec.mac)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_device(self._inventory.entry.entry_id, self._mac),
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> str:
        return self._inventory.get_remark(self._mac)

    async def async_set_value(self, value: str) -> None:
        self._inventory.set_remark(self._mac, value.strip())
