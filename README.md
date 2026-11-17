# ha-network-inventory

A small network inventory for [Home Assistant](https://www.home-assistant.io/).

It assigns every network device (taken from the Fritz!Box device trackers) to an **IP segment** and checks whether the device's IP address matches that segment.

- **Tag = HA label.** Each segment is a label with an IP range (e.g. "Network: IoT" → `192.168.189.0/24`).
- **DHCP range** as a separate setting: a device with an IP inside it has no fixed address yet.
- **Per device** (attached to the existing Fritz device):
  - `sensor.<device>_network_status`: `ok`, `dhcp`, `no_label`, `ambiguous`, `wrong_segment`, `outside`, `unknown`
  - `text.<device>_remark`: free text
- **Overview:** `sensor.network_inventory_problems`: number of devices that need attention, with details in the attributes (`problems`, `status`, `segments`).
- **Event** `ha_network_inventory_status_changed` on every status change and for new devices (`old_status: new`).

> Entity IDs depend on your Home Assistant language. With German as the system language they are e.g. `sensor.<device>_netzstatus` and `sensor.network_inventory_probleme`; states and attributes are always English.

## Status order

1. IP in the DHCP range → `dhcp`
2. no segment label → `no_label`
3. several segment labels → `ambiguous`
4. IP belongs to a different segment → `wrong_segment`
5. IP in no segment → `outside`
6. otherwise `ok`

Labels count on the device **or** on the tracker entity. Labels that are not assigned to a segment are ignored.
If a device is currently not present, its last known IP is used (attribute `ip_current: false`).

## Installation

**HACS / Marketplace (HA 2026.11+):** Add `https://github.com/nils-a/ha-network-inventory` as a custom repository (or via *Settings → Marketplace → Add from link*), install it and restart Home Assistant.

**Manual:** Copy the folder `custom_components/ha_network_inventory` to `config/custom_components/` and restart Home Assistant.

Then: *Settings → Devices & services → Add integration → Network Inventory*.

## Default segments

During setup you can let the integration create these segments and their labels:

| Label | Range |
| --- | --- |
| Network: Infrastructure | `192.168.188.1-192.168.188.49` |
| Network: Clients | `192.168.188.50-192.168.188.199` |
| Network: IoT | `192.168.189.0/24` |
| Network: Wireless/Mesh | `192.168.190.0/24` |

Everything can be changed later in the integration options.

## Event data

`ha_network_inventory_status_changed` carries `mac`, `name`, `ip`, `old_status`, `new_status`, `problem` (bool), `labels`, `device_id`, `tracker_entity_id` and `status_entity_id`.

## Specifying ranges

`192.168.189.0/24`, `192.168.188.1-192.168.188.49` or the short form `192.168.188.1-49`.
Segments must not overlap and must lie inside the network.

## Example automation

```yaml
triggers:
  - trigger: event
    event_type: ha_network_inventory_status_changed
    event_data:
      problem: true
actions:
  - action: notify.notify
    data:
      message: "{{ trigger.event.data.name }} ({{ trigger.event.data.ip }}): {{ trigger.event.data.new_status }}"
```
