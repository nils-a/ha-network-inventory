"""Constants for Network Inventory."""

from __future__ import annotations

DOMAIN = "ha_network_inventory"

CONF_NETWORK = "network"
CONF_DHCP = "dhcp_range"
CONF_SEGMENTS = "segments"
CONF_PLATFORMS = "tracker_platforms"
CONF_CREATE_DEFAULTS = "create_default_segments"

SEGMENT_LABEL = "label_id"
SEGMENT_RANGE = "range"

DEFAULT_NETWORK = "192.168.188.0/22"
DEFAULT_DHCP = "192.168.188.200-192.168.188.229"
DEFAULT_PLATFORMS = ["fritz"]

# (label name, range, icon, color)
DEFAULT_SEGMENTS = [
    ("Network: Infrastructure", "192.168.188.1-192.168.188.49", "mdi:server-network", "blue"),
    ("Network: Clients", "192.168.188.50-192.168.188.199", "mdi:laptop", "green"),
    ("Network: IoT", "192.168.189.0/24", "mdi:chip", "orange"),
    ("Network: Wireless/Mesh", "192.168.190.0/24", "mdi:radio-tower", "purple"),
]

# unique_id suffixes of the per-device entities
UID_STATUS = "network_status"
UID_REMARK = "remark"

STORAGE_KEY = f"{DOMAIN}.devices"
STORAGE_VERSION = 1

EVENT_STATUS_CHANGED = f"{DOMAIN}_status_changed"


def signal_device(entry_id: str, mac: str) -> str:
    """Dispatcher signal for one device."""
    return f"{DOMAIN}_{entry_id}_{mac}"


def signal_summary(entry_id: str) -> str:
    """Dispatcher signal for the summary sensor."""
    return f"{DOMAIN}_{entry_id}_summary"
