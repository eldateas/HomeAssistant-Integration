# ELDAT Plugin - Architektur-Dokumentation

## Übersicht

Das ELDAT Plugin ist ein Home Assistant Custom Component für die Integration von ELDAT EasyWave und EasyWave Neo Geräten über verschiedene Transceiver (RX11, RX21, etc.).

## Neue Ordnerstruktur (ab Januar 2026)

```
config/custom_components/eldat_plugin/
├── transceivers/                          # Transceiver und Device-Implementierungen
│   ├── base/                              # ✨ NEU: Abstrakte Basisklassen
│   │   ├── __init__.py                    # Exportiert alle Base-Klassen
│   │   ├── enums.py                       # Enums (DeviceType, OperatingMode, etc.)
│   │   ├── device_info.py                 # DeviceInfo, TransceiverCapabilities
│   │   ├── transceiver.py                 # BaseTransceiver, BaseDeviceHandler
│   │   ├── device.py                      # BaseDevice
│   │   ├── receiver.py                    # BaseReceiver
│   │   ├── transmitter.py                 # BaseTransmitter
│   │   └── sensor.py                      # BaseSensor
│   │
│   ├── behaviors/                         # ✨ NEU: Behavior Mixins
│   │   ├── __init__.py                    # Exportiert alle Mixins
│   │   ├── cover.py                       # CoverBehaviorMixin
│   │   ├── switch.py                      # SwitchBehaviorMixin
│   │   ├── light.py                       # LightBehaviorMixin
│   │   ├── sensor.py                      # SensorBehaviorMixin
│   │   ├── button.py                      # ButtonBehaviorMixin
│   │   └── entity_specs.py                # EntitySpecsMixin
│   │
│   ├── base.py                            # DEPRECATED: Backward compatibility wrapper
│   ├── factory.py                         # Transceiver Factory
│   │
│   └── rx11/                              # RX11 Transceiver-spezifische Implementation
│       ├── __init__.py
│       ├── transceiver.py                 # RX11Transceiver
│       ├── wrapper.py
│       ├── rx_module.py
│       │
│       └── devices/                       # RX11-Geräte
│           ├── registry.py                # Device Factory/Registry
│           │
│           ├── ew_receivers/              # EasyWave Receivers
│           │   ├── switch.py              # RX11SwitchReceiver
│           │   ├── motor.py               # RX11MotorReceiver
│           │   └── climate.py             # RX11ClimateReceiver
│           │
│           ├── ew_transmitters/           # EasyWave Transmitters
│           │   ├── single_button.py       # RX11SingleButtonTransmitter
│           │   ├── dual_button.py         # RX11DualButtonTransmitter
│           │   ├── triple_button.py       # RX11TripleButtonTransmitter
│           │   └── quad_button.py         # RX11QuadButtonTransmitter
│           │
│           ├── ewneo_sensors/             # EasyWave Neo Sensors
│           │   └── ewneo_sensor.py        # Universal EWneoSensor
│           │
│           └── ewneo_transceivers/        # EasyWave Neo Transceivers/Receivers
│               ├── transceiver.py         # RX11EWneoTransceiver
│               ├── switch.py              # RX11EWneoSwitch
│               ├── dual_switch.py         # RX11EWneoDualSwitch
│               ├── quad_switch.py         # RX11EWneoQuadSwitch
│               ├── dimmer.py              # RX11EWneoDimmer
│               ├── motor.py               # RX11EWneoMotor
│               ├── dual_motor.py          # RX11EWneoDualMotor
│               └── quad_motor.py          # RX11EWneoQuadMotor
│
├── __init__.py                            # Component Setup
├── config_flow.py                         # Configuration Flow
├── coordinator.py                         # Data Coordinator
├── device_manager.py                      # Device Management
├── [weitere Component-Dateien...]
```

## Klassen-Hierarchie

### 1. Basis-Hierarchie

```
BaseTransceiver (abstrakt)
  └── RX11Transceiver
      └── [zukünftig: RX21Transceiver, GatewayTransceiver]

BaseDevice (abstrakt)
  ├── BaseReceiver (abstrakt)
  │   ├── RX11SwitchReceiver
  │   ├── RX11MotorReceiver
  │   ├── RX11ClimateReceiver
  │   ├── RX11EWneoSwitch
  │   ├── RX11EWneoDualSwitch
  │   ├── RX11EWneoQuadSwitch
  │   ├── RX11EWneoDimmer
  │   ├── RX11EWneoMotor
  │   ├── RX11EWneoDualMotor
  │   ├── RX11EWneoQuadMotor
  │   └── RX11EWneoTransceiver
  │
  ├── BaseTransmitter (abstrakt)
  │   ├── RX11SingleButtonTransmitter
  │   ├── RX11DualButtonTransmitter
  │   ├── RX11TripleButtonTransmitter
  │   └── RX11QuadButtonTransmitter
  │
  └── BaseSensor (abstrakt)
      └── EWneoSensor (universal, kombinierbar)
```

### 2. Behavior Mixins (Mehrfachvererbung)

Mixins fügen Funktionalität hinzu:

```python
# Beispiel: Motor Receiver mit Cover-Verhalten
class RX11MotorReceiver(CoverBehaviorMixin, EntitySpecsMixin, BaseReceiver):
    pass

# Beispiel: Switch Receiver mit Switch-Verhalten
class RX11SwitchReceiver(SwitchBehaviorMixin, EntitySpecsMixin, BaseReceiver):
    pass

# Beispiel: Sensor mit Sensor-Verhalten
class EWneoSensor(SensorBehaviorMixin, EntitySpecsMixin, BaseSensor):
    pass
```

**Verfügbare Mixins:**
- `CoverBehaviorMixin` - Cover-Funktionen (open/close/stop/position)
- `SwitchBehaviorMixin` - Switch-Funktionen (on/off/toggle)
- `LightBehaviorMixin` - Light-Funktionen (on/off/brightness)
- `SensorBehaviorMixin` - Sensor-Daten-Verwaltung
- `ButtonBehaviorMixin` - Button-Event-Tracking
- `EntitySpecsMixin` - Automatische Entity-Specs Generierung

## Import-Patterns

### Für Device-Implementierungen (empfohlen):

```python
# Neue, klare Imports
from ...base import (
    BaseReceiver,
    DeviceType,
    DeviceSubtype,
    OperatingMode,
)
from ...behaviors import (
    SwitchBehaviorMixin,
    EntitySpecsMixin,
)
```

### Für Legacy-Code (backward compatible):

```python
# Alt - funktioniert weiterhin
from ....base import BaseReceiver, DeviceType, SwitchBehaviorMixin
```

## Device-Typen

### EasyWave (EW) - Einfache, unidirektionale Geräte

**Receiver:**
- Switch (Schalter)
- Motor (Rolladen/Jalousien)
- Climate (Heizung/Kühlung)

**Transmitter:**
- Single Button
- Dual Button
- Triple Button
- Quad Button

### EasyWave Neo (EWneo) - Bidirektionale Geräte

**Sensors:**
- Universal Sensor (kombinierbar: Temperatur, Feuchtigkeit, Wind, Regen)

**Receivers/Transceivers:**
- Transceiver (Universal)
- Switch (1-Kanal)
- Dual Switch (2-Kanal)
- Quad Switch (4-Kanal)
- Dimmer
- Motor (1-Kanal)
- Dual Motor (2-Kanal)
- Quad Motor (4-Kanal)

## Erweiterung des Plugins

### Neuen Device-Typ hinzufügen

1. **Basis-Klasse wählen**: BaseReceiver, BaseTransmitter oder BaseSensor
2. **Mixins auswählen**: Welches Verhalten wird benötigt?
3. **Datei erstellen**: Im passenden Ordner (ew_receivers/, ew_transmitters/, etc.)
4. **Registry aktualisieren**: In `registry.py` Factory-Funktion hinzufügen

Beispiel:

```python
# devices/ew_receivers/new_device.py
from ...base import BaseReceiver, DeviceType, DeviceSubtype, OperatingMode
from ...behaviors import SwitchBehaviorMixin, EntitySpecsMixin

class RX11NewDevice(SwitchBehaviorMixin, EntitySpecsMixin, BaseReceiver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, 
                        device_type=DeviceType.EW_RECEIVER,
                        subtype=DeviceSubtype.SWITCH,
                        **kwargs)
    
    @property
    def supported_entity_types(self) -> List[str]:
        return ["switch"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        # Implementation
        pass
    
    async def set_state(self, channel: int, state: Any) -> bool:
        # Implementation
        pass
    
    async def get_state(self, channel: int) -> Any:
        # Implementation
        pass
```

### Neuen Transceiver hinzufügen

1. **Ordner erstellen**: `transceivers/rx21/` (Beispiel)
2. **Transceiver-Klasse**: Erbt von `BaseTransceiver`
3. **Device-Ordner**: `rx21/devices/` mit eigener Registry
4. **Factory aktualisieren**: In `transceivers/factory.py`

## Best Practices

### 1. Klare Trennung der Verantwortlichkeiten

- **base/**: Nur abstrakte Klassen und Interfaces
- **behaviors/**: Nur wiederverwendbare Funktionalität
- **rx11/devices/**: Nur konkrete Device-Implementierungen

### 2. Naming Conventions

- **Klassen**: `RX11<Protokoll><Gerät>` (z.B. `RX11EWneoSwitch`)
- **Dateien**: lowercase_mit_unterstrichen.py
- **Ordner**: Plural (receivers, transmitters, sensors)

### 3. Mixins vor BaseDevice

```python
# Richtig: Mixins zuerst (Method Resolution Order)
class MyDevice(Mixin1, Mixin2, BaseDevice):
    pass

# Falsch: BaseDevice zuerst
class MyDevice(BaseDevice, Mixin1, Mixin2):
    pass
```

### 4. Imports minimieren

```python
# Gut: Nur was benötigt wird
from ...base import BaseReceiver, DeviceType
from ...behaviors import SwitchBehaviorMixin

# Schlecht: Wildcard-Imports
from ...base import *
```

## Migrations-Guide

Wenn Sie bestehenden Code aktualisieren:

1. **Imports prüfen**: `from ....base import` → `from ...base import`
2. **Mixins trennen**: Prüfen ob Mixins besser in separaten Dateien wären
3. **Testen**: Nach jeder Änderung testen!

## Vorteile der neuen Struktur

✅ **Übersichtlichkeit**: Jede Datei < 300 Zeilen
✅ **Wartbarkeit**: Klare Verantwortlichkeiten
✅ **Erweiterbarkeit**: Neue Geräte folgen gleichem Muster
✅ **Wiederverwendbarkeit**: Mixins können flexibel kombiniert werden
✅ **Testbarkeit**: Kleinere Module sind einfacher zu testen
✅ **Dokumentation**: Struktur ist selbsterklärend

## Support

Bei Fragen zur Architektur:
1. Lesen Sie diese Dokumentation
2. Schauen Sie sich Beispiele in `ew_receivers/` an
3. Konsultieren Sie den [REFACTORING_PLAN.md](../REFACTORING_PLAN.md)

---
*Letzte Aktualisierung: Januar 2026*
*Version: 2.0 (Refactored Architecture)*
