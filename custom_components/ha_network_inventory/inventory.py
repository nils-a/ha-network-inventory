"""Central inventory: finds network devices, evaluates their status, stores remarks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    SEGMENT_LABEL,
    SEGMENT_RANGE,
    CONF_SEGMENTS,
    CONF_DHCP,
    CONF_NETWORK,
    CONF_PLATFORMS,
    DEFAULT_NETWORK,
    DEFAULT_PLATFORMS,
    DOMAIN,
    EVENT_STATUS_CHANGED,
    STORAGE_KEY,
    STORAGE_VERSION,
    UID_REMARK,
    UID_STATUS,
    signal_device,
    signal_summary,
)
from .logic import (
    PROBLEM_STATUSES,
    STATUS_UNKNOWN,
    STATUSES,
    Segment,
    IpRange,
    evaluate,
    parse_ip,
)

_LOGGER = logging.getLogger(__name__)

STATUS_NEW = "new"


@dataclass
class DeviceRecord:
    """A network device found via a device tracker."""

    mac: str
    device_id: str
    tracker_entity_id: str


@dataclass(frozen=True)
class StatusResult:
    """Evaluated status of a device."""

    status: str
    ip: str | None
    ip_current: bool
    segment_ids: tuple[str, ...]


def _mac_from_device(device: dr.DeviceEntry) -> str | None:
    for conn_type, value in device.connections:
        if conn_type == dr.CONNECTION_NETWORK_MAC:
            return dr.format_mac(value)
    return None


class Inventory:
    """Keeps track of all network devices of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        opts = entry.options
        self.network = IpRange.parse(opts.get(CONF_NETWORK, DEFAULT_NETWORK))
        dhcp = opts.get(CONF_DHCP)
        self.dhcp = IpRange.parse(dhcp) if dhcp else None
        self.segments = [
            Segment(c[SEGMENT_LABEL], IpRange.parse(c[SEGMENT_RANGE]))
            for c in opts.get(CONF_SEGMENTS, [])
        ]
        self.platforms = set(opts.get(CONF_PLATFORMS, DEFAULT_PLATFORMS))

        self.devices: dict[str, DeviceRecord] = {}  # mac -> record
        self.results: dict[str, StatusResult] = {}  # mac -> result
        self._tracker_to_mac: dict[str, str] = {}
        self._last_real: dict[str, str] = {}
        self._pending_new: set[str] = set()
        self._started = False

        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict[str, Any]] = {}
        self._platform_callbacks: list[Callable[[list[DeviceRecord]], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._rescan_cancel: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------------ setup

    async def async_setup(self) -> None:
        stored = await self._store.async_load()
        self._data = dict((stored or {}).get("devices", {}))
        self._scan()
        self._remove_orphans()
        for mac in self.devices:
            self._recompute(mac)
        self._unsubs.append(
            self.hass.bus.async_listen(
                EVENT_STATE_CHANGED,
                self._handle_state_changed,
                event_filter=self._filter_state_changed,
            )
        )
        for event_type in (
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            lr.EVENT_LABEL_REGISTRY_UPDATED,
        ):
            self._unsubs.append(
                self.hass.bus.async_listen(event_type, self._handle_registry_event)
            )
        self._started = True

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._rescan_cancel is not None:
            self._rescan_cancel()
            self._rescan_cancel = None
        await self._store.async_save(self._data_to_save())

    @callback
    def register_platform(
        self, add_cb: Callable[[list[DeviceRecord]], None]
    ) -> CALLBACK_TYPE:
        """Register a platform; it gets all current devices and later new ones."""
        self._platform_callbacks.append(add_cb)
        add_cb(list(self.devices.values()))

        @callback
        def _unregister() -> None:
            self._platform_callbacks.remove(add_cb)

        return _unregister

    # --------------------------------------------------------------- scanning

    @callback
    def _scan(self) -> None:
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        found: dict[str, DeviceRecord] = {}
        for entry in ent_reg.entities.values():
            if (
                entry.domain != "device_tracker"
                or entry.platform not in self.platforms
                or entry.device_id is None
                or entry.disabled_by is not None
            ):
                continue
            device = dev_reg.async_get(entry.device_id)
            if device is None:
                continue
            mac = _mac_from_device(device)
            if mac is None:
                continue
            found[mac] = DeviceRecord(mac, entry.device_id, entry.entity_id)

        removed = [rec for mac, rec in self.devices.items() if mac not in found]
        added = [rec for mac, rec in found.items() if mac not in self.devices]
        self.devices = found
        self._tracker_to_mac = {rec.tracker_entity_id: mac for mac, rec in found.items()}

        now = dt_util.utcnow().isoformat()
        for rec in found.values():
            if "first_seen" not in self._data.setdefault(rec.mac, {}):
                self._data[rec.mac]["first_seen"] = now
                self._schedule_save()

        for rec in removed:
            self._remove_device(rec)
        if added and self._started:
            self._pending_new.update(rec.mac for rec in added)
            for add_cb in list(self._platform_callbacks):
                add_cb(added)

    @callback
    def _rescan(self, _now: Any = None) -> None:
        self._rescan_cancel = None
        self._scan()
        for mac in list(self.devices):
            self._recompute(mac)

    @callback
    def _remove_orphans(self) -> None:
        """Remove per-device entities whose tracker vanished while HA was down."""
        ent_reg = er.async_get(self.hass)
        for entity in er.async_entries_for_config_entry(ent_reg, self.entry.entry_id):
            unique_id = entity.unique_id or ""
            for suffix in (UID_STATUS, UID_REMARK):
                if unique_id.endswith(f"_{suffix}"):
                    if unique_id.removesuffix(f"_{suffix}") not in self.devices:
                        ent_reg.async_remove(entity.entity_id)
                    break

    @callback
    def _remove_device(self, rec: DeviceRecord) -> None:
        self.results.pop(rec.mac, None)
        self._last_real.pop(rec.mac, None)
        self._pending_new.discard(rec.mac)
        ent_reg = er.async_get(self.hass)
        for domain, suffix in (("sensor", UID_STATUS), ("text", UID_REMARK)):
            if entity_id := ent_reg.async_get_entity_id(domain, DOMAIN, f"{rec.mac}_{suffix}"):
                ent_reg.async_remove(entity_id)
        dev_reg = dr.async_get(self.hass)
        device = dev_reg.async_get(rec.device_id)
        if device is not None and self.entry.entry_id in device.config_entries:
            dev_reg.async_update_device(
                rec.device_id, remove_config_entry_id=self.entry.entry_id
            )
        async_dispatcher_send(self.hass, signal_summary(self.entry.entry_id))

    # ----------------------------------------------------------------- events

    @callback
    def _filter_state_changed(self, event_data: Mapping[str, Any]) -> bool:
        return event_data.get("entity_id") in self._tracker_to_mac

    @callback
    def _handle_state_changed(self, event: Event) -> None:
        if mac := self._tracker_to_mac.get(event.data["entity_id"]):
            self._recompute(mac)

    @callback
    def _handle_registry_event(self, event: Event) -> None:
        # Registry changes come in bursts; rescan once after one quiet second.
        if self._rescan_cancel is not None:
            self._rescan_cancel()
        self._rescan_cancel = async_call_later(self.hass, 1.0, self._rescan)

    # ------------------------------------------------------------- evaluation

    @callback
    def _labels_for(self, rec: DeviceRecord) -> set[str]:
        labels: set[str] = set()
        if entity := er.async_get(self.hass).async_get(rec.tracker_entity_id):
            labels |= set(entity.labels)
        if device := dr.async_get(self.hass).async_get(rec.device_id):
            labels |= set(device.labels)
        return labels

    @callback
    def _recompute(self, mac: str) -> None:
        rec = self.devices.get(mac)
        if rec is None:
            return
        stored = self._data.setdefault(mac, {})
        state = self.hass.states.get(rec.tracker_entity_id)
        ip = parse_ip(state.attributes.get("ip")) if state else None
        ip_current = ip is not None
        if ip is not None:
            if stored.get("last_ip") != str(ip):
                stored["last_ip"] = str(ip)
                self._schedule_save()
        else:
            ip = parse_ip(stored.get("last_ip"))

        status, segment_ids = evaluate(ip, self._labels_for(rec), self.segments, self.dhcp)
        result = StatusResult(status, str(ip) if ip else None, ip_current, segment_ids)
        old = self.results.get(mac)
        self.results[mac] = result
        if old != result:
            async_dispatcher_send(self.hass, signal_device(self.entry.entry_id, mac))
            async_dispatcher_send(self.hass, signal_summary(self.entry.entry_id))

        if status == STATUS_UNKNOWN:
            return
        last_real = self._last_real.get(mac)
        self._last_real[mac] = status
        if mac in self._pending_new:
            self._pending_new.discard(mac)
            self._fire(rec, result, STATUS_NEW)
        elif self._started and last_real is not None and last_real != status:
            self._fire(rec, result, last_real)

    @callback
    def _fire(self, rec: DeviceRecord, result: StatusResult, old_status: str) -> None:
        ent_reg = er.async_get(self.hass)
        self.hass.bus.async_fire(
            EVENT_STATUS_CHANGED,
            {
                "mac": rec.mac,
                "name": self.device_name(rec),
                "ip": result.ip,
                "old_status": old_status,
                "new_status": result.status,
                "problem": result.status in PROBLEM_STATUSES,
                "labels": [self.label_name(lid) for lid in result.segment_ids],
                "device_id": rec.device_id,
                "tracker_entity_id": rec.tracker_entity_id,
                "status_entity_id": ent_reg.async_get_entity_id(
                    "sensor", DOMAIN, f"{rec.mac}_{UID_STATUS}"
                ),
            },
        )

    # ---------------------------------------------------------------- helpers

    @callback
    def label_name(self, label_id: str) -> str:
        label = lr.async_get(self.hass).async_get_label(label_id)
        return label.name if label else label_id

    @callback
    def device_name(self, rec: DeviceRecord) -> str:
        device = dr.async_get(self.hass).async_get(rec.device_id)
        if device is None:
            return rec.mac
        return device.name_by_user or device.name or rec.mac

    @callback
    def segment_for(self, label_id: str) -> Segment | None:
        return next((c for c in self.segments if c.label_id == label_id), None)

    @callback
    def get_remark(self, mac: str) -> str:
        return self._data.get(mac, {}).get("remark", "")

    @callback
    def set_remark(self, mac: str, value: str) -> None:
        self._data.setdefault(mac, {})["remark"] = value
        self._schedule_save()
        async_dispatcher_send(self.hass, signal_device(self.entry.entry_id, mac))
        async_dispatcher_send(self.hass, signal_summary(self.entry.entry_id))

    @callback
    def device_attributes(self, mac: str) -> dict[str, Any]:
        rec = self.devices.get(mac)
        result = self.results.get(mac)
        if rec is None or result is None:
            return {}
        segment = self.segment_for(result.segment_ids[0]) if len(result.segment_ids) == 1 else None
        stored = self._data.get(mac, {})
        return {
            "ip": result.ip,
            "ip_current": result.ip_current,
            "labels": [self.label_name(lid) for lid in result.segment_ids],
            "expected_range": str(segment.range) if segment else None,
            "dhcp_range": str(self.dhcp) if self.dhcp else None,
            "mac": mac,
            "tracker": rec.tracker_entity_id,
            "remark": stored.get("remark", ""),
            "first_seen": stored.get("first_seen"),
        }

    @callback
    def summary(self) -> dict[str, Any]:
        counts = dict.fromkeys(STATUSES, 0)
        problems: list[dict[str, Any]] = []
        per_segment = {c.label_id: 0 for c in self.segments}
        entries = [
            (mac, result, self.devices[mac])
            for mac, result in self.results.items()
            if mac in self.devices
        ]
        entries.sort(key=lambda item: self.device_name(item[2]).lower())
        for mac, result, rec in entries:
            counts[result.status] += 1
            for lid in result.segment_ids:
                per_segment[lid] = per_segment.get(lid, 0) + 1
            if result.status in PROBLEM_STATUSES:
                problems.append(
                    {
                        "name": self.device_name(rec),
                        "status": result.status,
                        "ip": result.ip,
                        "mac": mac,
                        "labels": [self.label_name(lid) for lid in result.segment_ids],
                        "tracker": rec.tracker_entity_id,
                    }
                )
        return {
            "device_count": len(self.results),
            "status": counts,
            "problems": problems,
            "network": str(self.network),
            "dhcp_range": str(self.dhcp) if self.dhcp else None,
            "segments": [
                {
                    "labels": self.label_name(c.label_id),
                    "label_id": c.label_id,
                    "range": str(c.range),
                    "devices": per_segment.get(c.label_id, 0),
                }
                for c in self.segments
            ],
        }

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 10)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {"devices": self._data}
