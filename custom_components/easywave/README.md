# Easywave Integration for Home Assistant

[![License](https://img.shields.io/github/license/eldateas/HomeAssistant-Integration)](LICENSE)

Custom integration for Home Assistant to control and monitor ELDAT Easywave / EWneo radio devices via the **RX11 USB Transceiver**.

## Features

- Easywave transmitters (1/2/3/4-button, impulse & permanent modes)
- Easywave receivers (switches, motors, heating)
- EWneo bidirectional actuators (switches, motors)
- EWneo sensors (temperature, humidity)
- Device triggers for automations
- Automatic device backup & migration

## Documentation

- 🇬🇧 [English User Guide](docs/index.md)
- 🇩🇪 [Deutsche Anleitung](docs/index_de.md)

## Test Coverage

| Metric | Value |
|--------|-------|
| **Tests** | 774 |
| **Coverage** | 32% |
| **Framework** | pytest + pytest-homeassistant-custom-component |

Run tests locally:

```bash
pip install pytest pytest-asyncio pytest-homeassistant-custom-component pytest-cov
pytest --timeout=15 --cov=custom_components/easywave
```

## License

See [LICENSE](LICENSE) for details.
