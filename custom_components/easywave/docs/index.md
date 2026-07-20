# Easywave Integration – User Documentation

> **Version:** 0.7.0 | **Last Updated:** July 2026

🇩🇪 **[Deutsche Version](index_de.md)**

This documentation describes the setup and usage of the Easywave Integration for Home Assistant (e.g. **Home Assistant Green**) with the RX11 USB transceiver.

> **Note (0.7.0):** Devices are stored in config subentries (CORE-compatible). After upgrading from 0.6.x, recreate automations/device triggers. See [CHANGELOG.md](../CHANGELOG.md).

---

## Table of Contents

1. [Overview](#overview)
2. [Hardware Requirements](#hardware-requirements)
3. [Installation](#installation)
4. [Setting up the RX11 Transceiver](#setting-up-the-rx11-transceiver)
5. [Supported Device Types](#supported-device-types)
6. [Learning Devices](#learning-devices)
7. [Entity Types](#entity-types)
8. [Services](#services)
9. [Automations](#automations)
10. [Troubleshooting](#troubleshooting)

---

## Overview

The Easywave Integration enables control and monitoring of ELDAT radio devices via Home Assistant. Communication is handled through the **RX11 USB Transceiver**, which connects to your **Home Assistant Green** (or other Home Assistant host).

### How the RX11 Transceiver Works

```
┌─────────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│                     │   USB   │                  │   868   │                 │
│  Home Assistant     │◄───────►│  RX11 Transceiver│◄ ─ ─ ─ ►│  Easywave/EWneo │
│  (Green)            │         │                  │   MHz   │     Devices     │
└─────────────────────┘         └──────────────────┘         └─────────────────┘
```

**The RX11 Transceiver:**
- Receives button codes from Easywave transmitters (remote controls, wall switches)
- Sends button codes to Easywave receivers (switches, motors)
- Communicates bidirectionally with EWneo actuators (status feedback)
- Receives sensor data from EWneo sensors
- Runs in continuous receive mode

### Communication Behavior by Device Type

| Device Type | Communication | Description |
|-------------|---------------|-------------|
| **Easywave Transmitter** | Send → RX11 | Send button codes when pressed. Dead-man operation possible (press & release detectable) |
| **Easywave Receiver** | RX11 → Receive | Receive button codes from RX11. **No state is sent back** – the current switch state is unknown to Home Assistant |
| **EWneo Actuators** | RX11 ↔ Bidirectional | Full two-way communication. Actuators report their current state back (position, on/off, etc.) |
| **EWneo Sensors** | Send → RX11 | Periodically send measurements (temperature, humidity, etc.). No bidirectional communication |

---

## Hardware Requirements

| Component | Requirement |
|-----------|-------------|
| **Home Assistant** | Version 2024.1.0 or higher |
| **Hardware** | Home Assistant Green, Yellow, or other Linux-based host |
| **RX11 USB Transceiver** | VID: 0x155A, PID: 0x1014 |

### RX11 Hardware Identification

```bash
# Check USB device
lsusb | grep 155a

# Expected output:
# Bus xxx Device xxx: ID 155a:1014 ELDAT GmbH RX11 USB Transceiver
```

---

## Installation

### HACS Installation (recommended)

1. Open **HACS** in Home Assistant
2. Navigate to **Integrations**
3. Click **⋮** (three dots) → **Custom repositories**
4. Add: `https://github.com/eldateas/HomeAssistant-Integration`
5. Category: `Integration`
6. Search for **"Easywave"** and install
7. **Restart Home Assistant**

### Manual Installation

```bash
# Change to config directory
cd /config/custom_components/

# Clone repository or copy files
```

---

## Setting up the RX11 Transceiver

### Initial Setup

1. **Connect RX11** – Connect the USB transceiver to a USB port on your Home Assistant Green
2. **Add Integration** – *Settings → Devices & Services → Add Integration → "Easywave"*
3. **Automatic Detection** – The RX11 is automatically detected and displayed
4. **Confirm Selection** – Select the detected transceiver

### After Setup

After successful setup, the RX11 is displayed as a device in Home Assistant with:
- **Hardware Version** (e.g. "ELDATEAS UNS H01")
- **Firmware Version** (e.g. "1.0")
- **Connection Status**

---

## Supported Device Types

### Easywave Transmitters (unidirectional → RX11)

| Device Type | Description | Communication |
|-------------|-------------|---------------|
| `ew_transmitter` | Remote controls, wall switches, handheld transmitters | Send button codes (A, B, C, D) to the RX11. **Dead-man reception possible:** Press and release are detected separately |

> 💡 **Note:** Easywave transmitters can be used as pulse switches (short press) or in dead-man mode (while pressed).

### Easywave Receivers (RX11 → unidirectional)

| Device Type | Description | Communication |
|-------------|-------------|---------------|
| `ew_receiver` | Switches, motors, dimmers, heating actuators | Receive button codes from RX11. **No state is sent back** |

> ⚠️ **Important:** Easywave receivers **do not send state back**. Home Assistant does not know the current switch state and can only "assume" it. Manual operation at the device may cause the state in Home Assistant to differ.

### EWneo Actuators (bidirectional ↔ RX11)

| Device Type | Code | Description |
|-------------|------|-------------|
| `ewneo_switch` | 0x03 | Single-channel switch with status feedback |
| `ewneo_motor` | 0x05 | Motor/blind with position feedback |
| `ewneo_dual_switch` | 0x06 | Dual-channel switch |
| `ewneo_quad_switch` | 0x07 | Quad-channel switch |
| `ewneo_dual_motor` | 0x08 | Dual-channel motor |
| `ewneo_quad_motor` | 0x09 | Quad-channel motor |

> ✅ **Advantage:** EWneo actuators communicate **bidirectionally**. The current device state (on/off, position, etc.) is reported back to Home Assistant – even with manual operation.

### EWneo Sensors (unidirectional → RX11)

| Sensor Type | Measurements | Communication |
|-------------|--------------|---------------|
| **Temperature Sensor** | Temperature (°C) | Periodic sending |
| **Temperature / Humidity Sensor** | Temperature, Humidity | Periodic sending |

> 📡 **Note:** EWneo sensors send their measurements periodically to the RX11. They do not receive commands (no bidirectional communication).

---

## Learning Devices

### Learning Easywave Transmitters

**Application:** Remote controls, wall switches, handheld transmitters

1. In the device dialog, select **"Easywave Transmitter"**
2. **Select operation mode:**
   - **1-Button operation:** One button per function (e.g. toggle)
   - **2-Button operation:** Two buttons per function (e.g. on/off or up/down)
   - **3-Button operation:** Three buttons per function (e.g. up/stop/down)
3. **Select control type** (only for 1-button operation):
   - **Individual:** Each button creates its own object (each button represents its own state)
   - **Group:** All buttons create one shared object (buttons change the state)
4. **Configure behavior** (only for 1-button operation):
   - **Pulse:** State returns to "not pressed" after release
   - **Permanent:** The last state (pressed/not pressed) is retained
5. **Select button count** (only for 1-button operation) – Number of buttons on the transmitter (1, 2, 3 or 4)
6. **Press transmitter button** – Press the button to be learned
7. Confirm learning

**Operation Modes in Detail:**

| Mode | Buttons | Description | Typical Use |
|------|---------|-------------|-------------|
| 1-Button | 1 | One button toggles the state | Light switch, doorbell |
| 2-Button | 2 | Button A = On/Up, Button B = Off/Down | Blind switch, light switch |
| 3-Button | 3 | Button A = Up, Button B = Stop, Button C = Down | Blind control |

**Control Type (only 1-button operation):**

| Option | Objects | Description | Typical Use |
|--------|---------|-------------|-------------|
| Individual | Per button | Each button is represented as a separate object | Multiple independent switches |
| Group | One object | All buttons control one shared object | One device with multiple buttons |

**Behavior Modes (only 1-button operation):**

| Option | Description | Typical Use |
|--------|-------------|-------------|
| Pulse | State changes on press, returns after release | Doorbell, door opener |
| Permanent | State remains after button press | Light switch |

### Learning Easywave Receivers

**Application:** Switches, relays, heating actuators

1. Select **"Easywave Receiver"** in the dialog
2. **Select operating mode:**
   - **PULSE (1-Button):** Short switching pulse of 1s
   - **ON/OFF (2-Button):** Switch relay output ON or OFF
   - **UP/DOWN (2-Button):** Switch relay output UP or DOWN
   - **UP/STOP/DOWN (3-Button):** Commands for tubular motors (up/stop/down)
   - **ON/OFF (Heating):** ON/OFF with 4h repeat of last state
   - **UNIVERSAL (4-Button):** Switching pulses for all 4 transmit codes
3. **Put receiver into programming mode** (on the device, matching the selected operating mode)
4. Confirm **Send learning command**
5. Confirm successful pairing

### Learning Easywave neo Receivers (bidirectional)

**Application:** Modern Easywave neo actuators with bidirectional communication and status feedback

1. Select **"Easywave neo Receiver"** in the dialog
2. **Put receiver into programming mode:**
   - Put the receiver into programming mode for any operating mode
   - The LED on the receiver should blink
3. Click **"Next"** – The RX11 sends a pairing request
4. **Wait for confirmation** – The device responds with its device data
5. **Assign device name** and optionally assign an area
6. Confirm learning

> ✅ **Advantage:** Easywave neo receivers are automatically detected. The device type (switch, motor, dimmer) and channel count are transmitted by the device itself.

### Learning Easywave neo Sensors

**Application:** Temperature, humidity, and climate sensors

1. Select **"Easywave neo Sensor"** in the dialog
2. Click **"Learn sensor"**
3. **Press the learn button on the sensor** – Press the learn button on the back of the sensor
4. **Wait for reception** – Home Assistant automatically detects the available measurement data
5. **Assign device name** and optionally assign an area
6. Confirm learning

> 📡 **Note:** Home Assistant automatically detects which measurement data the sensor provides (temperature, humidity, etc.).

---

## Entity Types

The integration automatically creates appropriate entities for each device:

| Entity Type | Platform | Usage |
|-------------|----------|-------|
| **switch** | Switch | On/off control |
| **light** | Light | Dimmer with brightness |
| **cover** | Cover | Blinds, shutters |
| **sensor** | Sensor | Measurements, status |
| **binary_sensor** | Binary sensor | Button state, battery |
| **button** | Button | Trigger actions |
| **select** | Select | Mode switching |

### Automatically Created Entities

**Example: EWneo Motor**
- `cover.ewneo_motor_[serial]` – Main control
- `sensor.ewneo_motor_[serial]_position` – Current position
- `sensor.ewneo_motor_[serial]_status` – Movement status
- `button.ewneo_motor_[serial]_remove` – Remove device

**Example: Easywave Transmitter (4 buttons)**
- `binary_sensor.ew_transmitter_[serial]_button_a` – Button A
- `binary_sensor.ew_transmitter_[serial]_button_b` – Button B
- `binary_sensor.ew_transmitter_[serial]_button_c` – Button C
- `binary_sensor.ew_transmitter_[serial]_button_d` – Button D
- `sensor.ew_transmitter_[serial]_last_button` – Last pressed button

---

## Services

### Device Management

| Service | Description |
|---------|-------------|
| `easywave.add_device` | Manually add device |
| `easywave.remove_device` | Remove device |
| `easywave.remove_device_by_id` | Remove device by Device ID |
| `easywave.list_removable_devices` | List removable devices |
| `easywave.bulk_remove_devices` | Remove multiple devices |

### Device Control

| Service | Description |
|---------|-------------|
| `easywave.send_command` | Send command to device |
| `easywave.scan_devices` | Start device scan |
| `easywave.learn_device` | Learn device |

### Maintenance

| Service | Description |
|---------|-------------|
| `easywave.refresh_entity_specs` | Update entity specifications |
| `easywave.reset_entity_registry` | Reset entity registry |
| `easywave.cleanup_orphaned_entities` | Clean up orphaned entities |
| `easywave.fix_transceiver` | Transceiver repair |

### Example: Manually Add Device

```yaml
service: easywave.add_device
data:
  serial_number: "1234567890ABCDEF"
  device_type: "ewneo_motor"
  device_name: "Living Room Blind"
```

---

## Automations

### Using Transmitter as Trigger

```yaml
automation:
  - alias: "Toggle light with remote"
    trigger:
      - platform: state
        entity_id: binary_sensor.ew_transmitter_abc123_button_a
        to: "on"
    action:
      - service: light.toggle
        target:
          entity_id: light.living_room
```

### Motor Position at Sunset

```yaml
automation:
  - alias: "Close blinds at sunset"
    trigger:
      - platform: sun
        event: sunset
    action:
      - service: cover.set_cover_position
        target:
          entity_id: cover.ewneo_motor_abc123
        data:
          position: 20
```

### Temperature-Controlled Heating

```yaml
automation:
  - alias: "Turn on heating when cold"
    trigger:
      - platform: numeric_state
        entity_id: sensor.ewneo_sensor_abc123_temperature
        below: 18
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.ew_receiver_heating
```

### Battery Warning

```yaml
automation:
  - alias: "Transmitter battery warning"
    trigger:
      - platform: state
        entity_id: binary_sensor.ew_transmitter_abc123_battery_warning
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          title: "Low battery"
          message: "The transmitter battery is almost empty."
```

---

## Troubleshooting

### RX11 Not Detected

1. **Check USB connection:**
   ```bash
   lsusb | grep 155a
   dmesg | tail -20
   ```

2. **Check permissions:**
   ```bash
   ls -la /dev/ttyUSB* /dev/ttyACM*
   # Add user to dialout group:
   sudo usermod -aG dialout homeassistant
   ```

3. **Container mode:** Pass USB device through in Docker/Addon

### Device Not Responding

1. **Check range** – Move device closer to RX11
2. **Check battery** – For battery-powered devices
3. **Re-learn** – Remove device and learn again
4. **Check logs:**
   ```yaml
   logger:
     logs:
       custom_components.easywave: debug
       custom_components.easywave.transceivers.rx11: debug
   ```

### Missing Entities

1. **Reload integration:** *Settings → Devices & Services → Easywave → ⋮ → Reload*
2. **Run service:** `easywave.refresh_entity_specs`
3. **Restart Home Assistant**

### Transceiver Errors

For persistent communication problems:
```yaml
service: easywave.fix_transceiver
data: {}
```

---

## Data Security

### Automatic Backups

The integration automatically backs up device configurations:
- **Location:** `managed_devices.json`
- **Backup on every save**
- **Automatic migration** on updates

### Update Safety

✅ Configured devices are preserved during updates  
✅ No manual reconfiguration required  
✅ Centralized device management (from v2.0)

---

## Further Information

- **GitHub Repository:** [eldateas/HomeAssistant-Integration](https://github.com/eldateas/HomeAssistant-Integration)
- **Issue Tracker:** [Report Issues](https://github.com/eldateas/HomeAssistant-Integration/issues)
- **ELDAT Website:** [eldat.de](https://www.eldat.de)

---

*This documentation refers to Easywave Integration version 0.6.x*

