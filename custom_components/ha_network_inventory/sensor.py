"""Sensors: status per device and a summary of all problems."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceEntryType,
    DeviceInfo,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NetworkInventoryConfigEntry
from .const import DOMAIN, UID_STATUS, signal_device, signal_summary
from .inventory import DeviceRecord, Inventory
from .logic import PROBLEM_STATUSES, STATUSES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkInventoryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    inventory = entry.runtime_data
    async_add_entities([ProblemSensor(inventory)])

    @callback
    def _add(records: list[DeviceRecord]) -> None:
        async_add_entities(StatusSensor(inventory, rec) for rec in records)

    entry.async_on_unload(inventory.register_platform(_add))


class StatusSensor(SensorEntity):
    """Network status of one device."""

    _attr_has_entity_name = True
    _attr_translation_key = "network_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES
    _attr_should_poll = False

    def __init__(self, inventory: Inventory, rec: DeviceRecord) -> None:
        self._inventory = inventory
        self._mac = rec.mac
        self._attr_unique_id = f"{rec.mac}_{UID_STATUS}"
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
    def native_value(self) -> str | None:
        result = self._inventory.results.get(self._mac)
        return result.status if result else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._inventory.device_attributes(self._mac)


class ProblemSensor(SensorEntity):
    """Number of devices that need attention, with details as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "problems"
    _attr_should_poll = False

    def __init__(self, inventory: Inventory) -> None:
        self._inventory = inventory
        entry_id = inventory.entry.entry_id
        self._attr_unique_id = f"{entry_id}_problems"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Network Inventory",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_summary(self._inventory.entry.entry_id),
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> int:
        return sum(
            1
            for result in self._inventory.results.values()
            if result.status in PROBLEM_STATUSES
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._inventory.summary()
