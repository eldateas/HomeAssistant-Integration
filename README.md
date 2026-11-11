# Easywave for Home Assistant

Custom integration for ELDAT Easywave and EWneo devices in Home Assistant via the RX11 USB Transceiver.

## Features

- Control and monitor ELDAT radio devices (868 MHz)
- Support for Easywave transmitters, receivers, and Easywave neo actuators/sensors
- Bidirectional communication with Easywave neo devices (status feedback)
- Easy device learning via Home Assistant UI

## Hardware Requirements

| Component | Requirement |
|-----------|-------------|
| **Home Assistant** | Version 2026.11.0 or higher |
| **Hardware** | Home Assistant Green, Yellow, or other Linux-based host |
| **RX11 USB Transceiver** | VID: 0x155A, PID: 0x1014 |

## Installation

### Marketplace (recommended)

Home Assistant includes the Marketplace under **Settings → Marketplace**.

1. Open **Settings → Marketplace**
2. Select **Add from link**
3. Enter `https://github.com/eldateas/HomeAssistant-Integration` and select **Add**
4. Open **Easywave** and install it
5. **Restart Home Assistant**

### Manual Installation

Copy the `custom_components/easywave` folder to your Home Assistant `config/custom_components/` directory and restart Home Assistant.

## Configuration

Configure through the Home Assistant UI under **Settings → Devices & Services → Add Integration → Easywave**.

## Documentation

📖 **[Full User Documentation (English)](custom_components/easywave/docs/index.md)**

🇩🇪 **[Deutsche Dokumentation](custom_components/easywave/docs/index_de.md)**

## License

See [LICENSE](LICENSE) for details.
