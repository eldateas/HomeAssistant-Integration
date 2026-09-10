# Easywave Integration Changelog

## 0.7.7 — EWneo sensor scaling (library 0.3.4)

### Changed
- Requires `easywave-home-control==0.3.4` — STH01 (NEO default) keeps verified
  air-interface `/100` centi-units. Library also implements RX21 Table 6 scaling for
  `LEGACY_RX21` (Kelvin `n/20`, humidity `100·n/4095`); HA uses NEO only.

## 0.7.6 — Neo actuator command and restore fixes

### Changed
- Requires `easywave-home-control==0.3.1` (dual/quad motor summary exposes `position`).

### Fixed
- Dual/quad neo **switches**: send `MultiSwitchChangeCommand` on mode 0 (was `SwitchChangeCommand`).
- Dual/quad neo **motors**: send `MultiMotorMoveCommand` on mode 0; read position from mode-0 summary via library 0.3.1.
- Neo switch/dimmer **state restore** after restart: query mode 0 once entities exist (coordinator bulk restore ran too early).
- Motors keep full-mode restore (`0` / `2` / `10` / `18` / `26`) for **runtime measurement** (`SET_POSITION`).
- Gateway status sensor: use `async_listen` for `homeassistant_started` to avoid “Unable to remove unknown job listener” on unload.

## 0.7.5 — Area selection when learning devices

### Added
- Optional **area** picker on the learn confirm step for transmitters, neo sensors, receivers, and neo actuators.
- Best-effort preselection when Home Assistant provides an area in the flow context (e.g. add from an area).

### Changed
- The chosen area is applied once via the **device registry**; it is **not** stored in `CONF_DEVICES` bucket data, so HACS→CORE storage stays schema-compatible.

## 0.7.4 — Clean delete for integration and subentries

### Fixed
- Deleting the integration or a device-bucket subentry no longer revives devices from archived JSON under `config/easywave/migrated/`.
- Removed devices are purged from recorder/logbook history, removed from the device registry, and not restored from registry tombstones.

### Changed
- One-shot JSON migration sets `json_migration_done` on the hub entry after the first run.
- Removing the integration deletes the `config/easywave` data directory (live + migrated archives).

## 0.7.2 — Device info, translations, and delete cleanup

### Fixed
- Crash on setup under Home Assistant **2026.3.x**: `ConfigEntry.get_subentries_of_type` does not exist yet (added later). Use a compatible iterator over `entry.subentries` so migration and device buckets work on the advertised minimum version.
- Receiver operating-mode menu texts no longer use developer jargon (“Optimistic …”); wording matches **0.6.x** again (DE/EN/FR).
- Receiver learn flow again shows the prepare → send Code A → LED acknowledgement steps for **all** receiver kinds (was skipping straight to naming).

### Changed
- Device **model** strings in the device info panel match **0.6.10** again (localized type designation, e.g. `Easywave Empfänger, EIN/AUS`). Radio serial numbers are not shown on child devices.
- Manufacturer label (“von Eldat” / “by ELDAT”) is no longer set on child devices.

### Added
- When deleting a device via the three-dot menu, **recorder/logbook history** for that device’s entities is purged (`keep_days: 0`), as in 0.6.10. Storage remains CORE-compatible.

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
