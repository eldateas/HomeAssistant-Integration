# Easywave Integration Changelog

## 0.7.2 — HA 2026.3 subentry compatibility

### Fixed
- Crash on setup under Home Assistant **2026.3.x**: `ConfigEntry.get_subentries_of_type` does not exist yet (added later). Use a compatible iterator over `entry.subentries` so migration and device buckets work on the advertised minimum version.

## 0.7.1 — Fix 0.6.10 hub upgrade

### Fixed
- Config entry **VERSION** set to **2** (same as HACS 0.6.x). VERSION 1 rejected existing hubs with “version higher than current”, so the RX11 never loaded and JSON→subentry migration never ran.
- Hub unique_id `rx11_*` normalized to CORE-style `easywave_*`.
- JSON migration can recover devices from `config/easywave/migrated/` if live JSON was already archived.

## 0.7.0 — CORE storage architecture (breaking)

### Breaking changes
- Device persistence moved from JSON files (`registered_devices.json` / `managed_devices.json`) to **Home Assistant config subentries** (CORE-compatible bucket model).
- Entity unique IDs are now **serial-stable** (`transmitter_{serial}_…`, `receiver_{serial}_…`, `ewneo_*_{serial}_…`). Automations and device triggers must be recreated.
- Protocol stack is now the PyPI library `easywave-home-control` (in-tree `transceivers/` removed).

### Migration
- On first setup after upgrade, existing devices are imported from JSON into subentries once.
- Legacy JSON files are archived under `config/easywave/migrated/`.
- A repair issue reminds you to recreate automations/triggers.

### Features (parity with previous HACS device surface)
- Subentry buckets: transmitter, neo sensor, **receiver**, **neo actuator**
- Platforms: sensor, binary_sensor, button, switch, light, cover, device triggers
- EW receivers: impulse, switch, cover, motor, heating, universal
- EWneo actuators: switch / dimmer / motor (1/2/4 channel)
- Expanded transmitter config flow (1/2/3-button operating types, grouping, switch mode)

### Requirements
- `easywave-home-control==0.3.0`
- `pyserial==3.5`
