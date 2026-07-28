# Easywave (HACS)

Custom integration for ELDAT Easywave / EWneo via the RX11 USB transceiver.

**Version:** 0.7.3

## Architecture

This release aligns storage and identity with the upcoming Home Assistant **CORE** Easywave integration:

- One hub config entry (RX11)
- Devices stored in typed **bucket subentries** (`easywave_transmitter`, `easywave_neo_sensor`, `easywave_receiver`, `easywave_neo_actuator`)
- Serial-stable device / entity IDs for a later seamless CORE switch
- Protocol via [`easywave-home-control`](https://pypi.org/project/easywave-home-control/)

## Upgrade from 0.6.x

1. Update the integration.
2. Restart Home Assistant.
3. Devices are migrated automatically from JSON → subentries.
4. Recreate automations and device triggers (entity unique IDs changed).

Archived JSON files land in `config/easywave/migrated/`.

## Supported devices

| Type | Platforms |
|------|-----------|
| EW transmitter | sensor / binary_sensor + triggers |
| EWneo sensor | sensor |
| EW receiver | button / switch / cover |
| EWneo switch / dimmer / motor | switch / light / cover |

## Docs

See [docs/index.md](docs/index.md).

## Validation (CI & local)

GitHub Actions workflow: [`.github/workflows/validate.yaml`](../../.github/workflows/validate.yaml)

- **Hassfest** — Home Assistant integration structure/manifest
- **HACS** — same checks used for default-repository inclusion
- **Unit tests** — `tests/easywave`

Locally (from the repository root):

```bash
./scripts/validate.sh           # hassfest + HACS (if Docker) + tests
./scripts/validate.sh hassfest  # only hassfest
./scripts/validate.sh hacs      # only HACS (needs Docker)
./scripts/validate.sh tests     # only unit tests
```

Without Docker, hassfest falls back to a sibling `HomeAssistant-Core` checkout (`CORE_PATH` override possible).

Brand icons/logos ship in `custom_components/easywave/brand/` (Home Assistant 2026.3+). A PR to `home-assistant/brands` is **not** required for HACS custom integrations.

For HACS without GitHub API access:

```bash
export HACS_IGNORE="description topics issues archived"
./scripts/validate.sh hacs
```

For full HACS checks (recommended before a `hacs/default` PR):

```bash
export GITHUB_TOKEN=ghp_…
./scripts/validate.sh hacs
```
