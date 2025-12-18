# ELDAT Plugin - Strukturierte Device-Architektur

## Übersicht

Das ELDAT Plugin wurde refaktoriert, um eine strukturierte, typisierte und erweiterbere Geräteverwaltung zu bieten. Die neue Architektur trennt klar zwischen Transceiver-Typen, Gerätekategorien, Subtypen und Betriebsarten.

## Architektur-Übersicht

```
transceivers/
├── base.py                    # Abstrakte Basisklassen
├── factory.py                 # Transceiver-übergreifende Factory
└── rx11/                      # RX11 Transceiver spezifische Implementierung
    ├── devices/
    │   ├── ew_receivers.py    # EW-Receiver (Motor/Switch/Dimmer/Heating+Cooling)
    │   ├── ew_transmitters.py # EW-Transmitter (1-Tast/2-Tast/3-Tast/4-Tast)
    │   ├── ewneo_sensors.py   # EWneo-Sensor (Temperature/Humidity/Motion/Door)
    │   ├── ewneo_receivers.py # EWneo-Receiver (Switch/Dimmer/Motor)
    │   └── registry.py        # RX11 Device Factory
    └── transceiver.py         # RX11 Transceiver Implementation
```

## Device-Hierarchie

### 1. Transceiver-Typen
- **RX11**: Aktuell unterstützter Transceiver
- **RX21**: Zukünftige Erweiterung
- **Gateway**: Zukünftige Erweiterung

### 2. Device-Typen (DeviceType)
- **EW_RECEIVER**: EasyWave Empfänger
- **EW_TRANSMITTER**: EasyWave Sender  
- **EW_SENSOR**: EasyWave Sensoren
- **EWNEO_RECEIVER**: EasyWave Neo Empfänger
- **EWNEO_SENSOR**: EasyWave Neo Sensoren

### 3. Device-Subtypen (DeviceSubtype)
#### Receiver Subtypen:
- **MOTOR**: Rolladen/Jalousie-Steuerung
- **SWITCH**: Ein/Aus-Schalter
- **DIMMER**: Dimmbares Licht
- **HEATING_COOLING**: Heizungs-/Kühlungssteuerung

#### Sensor Subtypen:
- **TEMPERATURE**: Temperatursensor
- **HUMIDITY**: Feuchtigkeitssensor  
- **MOTION**: Bewegungsmelder
- **DOOR_WINDOW**: Tür-/Fenstersensor

#### Transmitter Subtypen:
- **SINGLE_BUTTON**: Ein-Tasten-Fernbedienung
- **DUAL_BUTTON**: Zwei-Tasten-Fernbedienung
- **QUAD_BUTTON**: Vier-Tasten-Fernbedienung

### 4. Betriebsarten (OperatingMode)
#### Transmitter Modi:
- **ONE_BUTTON**: 1-Tast-Betrieb
- **TWO_BUTTON**: 2-Tast-Betrieb
- **THREE_BUTTON**: 3-Tast-Betrieb
- **FOUR_BUTTON**: 4-Tast-Betrieb

#### Receiver Modi:
- **SINGLE_CHANNEL**: Ein-Kanal-Betrieb
- **DUAL_CHANNEL**: Zwei-Kanal-Betrieb
- **QUAD_CHANNEL**: Vier-Kanal-Betrieb

#### Sensor Modi:
- **CONTINUOUS**: Kontinuierliche Messung
- **PERIODIC**: Periodische Messung
- **EVENT_TRIGGERED**: Ereignis-gesteuert

## Klassen-Struktur

### Abstrakte Basisklassen

```python
BaseDevice
├── BaseReceiver
│   ├── RX11EWReceiver
│   │   ├── RX11EWReceiverSwitch
│   │   ├── RX11EWReceiverDimmer
│   │   ├── RX11EWReceiverMotor
│   │   └── RX11EWReceiverHeatingCooling
│   └── RX11EWneoReceiver
│       ├── RX11EWneoReceiverSwitch
│       ├── RX11EWneoReceiverDimmer
│       └── RX11EWneoReceiverMotor
├── BaseTransmitter
│   └── RX11EWTransmitter
│       ├── RX11EWTransmitterSingleButton
│       ├── RX11EWTransmitterDualButton
│       └── RX11EWTransmitterQuadButton
└── BaseSensor
    └── RX11EWneoSensor
        ├── RX11EWneoTemperatureSensor
        ├── RX11EWneoHumiditySensor
        ├── RX11EWneoMotionSensor
        └── RX11EWneoDoorWindowSensor
```

## Factory Pattern

Die Device-Erstellung erfolgt über ein mehrstufiges Factory-Pattern:

1. **EldatDeviceFactory** (global): Koordiniert alle Transceiver-Typen
2. **RX11DeviceFactory**: Spezifisch für RX11-Geräte
3. **Spezialisierte Factory-Funktionen**: Für jeden Device-Typ

```python
from eldat_plugin.device_factory import device_factory

# Automatische Device-Erstellung
device = device_factory.create_device(
    serial_number="1234567890ABCDEF",
    device_info={
        "type": "ew_transmitter",
        "name": "Wohnzimmer Fernbedienung",
        "button_count": 4
    },
    transceiver_type=TransceiverType.RX11
)
```

## Migration

Die neue Architektur ist vollständig rückwärtskompatibel. Ein Migration-Tool wandelt bestehende Geräte automatisch um:

```python
from eldat_plugin.migration import DeviceArchitectureMigrator

migrator = DeviceArchitectureMigrator(legacy_coordinator)
structured_coordinator = await migrator.create_structured_coordinator()
results = await migrator.migrate_all_devices()
```

## Structured Coordinator

Der neue `StructuredEldatCoordinator` erweitert den bestehenden Coordinator um strukturierte Device-Verwaltung:

```python
# Zugriff auf strukturierte Devices
structured_devices = coordinator.get_all_structured_devices()

# Einzelnes Device abrufen
device = coordinator.get_structured_device(serial_number)

# Device-spezifische Eigenschaften
if isinstance(device, BaseTransmitter):
    button_count = device.button_count
    operating_mode = device.operating_mode
elif isinstance(device, BaseReceiver):
    channels = device.channels
    learned_transmitters = device.learned_transmitters
```

## Entity-Erstellung

Entities werden automatisch basierend auf dem Device-Typ und Subtyp erstellt:

### EW-Transmitter → Binary Sensors
```python
# Automatisch für jeden Button
for button_id in range(device.button_count):
    binary_sensor = StructuredTransmitterButtonSensor(
        coordinator, serial_number, device, button_id
    )
```

### EW-Receiver → Switch/Light/Cover/Climate
```python
# Je nach Subtyp
if device.subtype == DeviceSubtype.SWITCH:
    # Switch Entity
elif device.subtype == DeviceSubtype.DIMMER:
    # Light Entity  
elif device.subtype == DeviceSubtype.MOTOR:
    # Cover Entity
elif device.subtype == DeviceSubtype.HEATING_COOLING:
    # Climate Entity
```

### EWneo-Sensors → Sensor/Binary Sensor
```python
# Je nach Sensor-Typ
if device.subtype == DeviceSubtype.TEMPERATURE:
    # Temperature Sensor Entity
elif device.subtype == DeviceSubtype.MOTION:
    # Motion Binary Sensor Entity
```

## Vorteile der neuen Architektur

1. **Typisierung**: Vollständig typisierte Enums und Klassen
2. **Erweiterbarkeit**: Einfache Erweiterung um neue Transceiver/Device-Typen
3. **Klarheit**: Eindeutige Zuordnung von Gerät → Typ → Subtyp → Betriebsart
4. **Wartbarkeit**: Getrennte Implementierungen pro Gerätekategorie
5. **Testbarkeit**: Jede Klasse ist isoliert testbar
6. **Rückwärtskompatibilität**: Bestehende Funktionalität bleibt erhalten

## Beispiel-Verwendung

```python
# Device-Analyse
analysis = device_factory.analyze_device({
    "type": "ew_receiver", 
    "name": "Küche Dimmer",
    "channels": 1
})
# Ergebnis: DeviceType.EW_RECEIVER, DeviceSubtype.DIMMER, OperatingMode.SINGLE_CHANNEL

# Device-Erstellung
device = rx11_device_factory.create_device(
    "1234567890ABCDEF",
    {"type": "ew_receiver", "name": "Küche Dimmer", "subtype": "dimmer"}
)
# Ergebnis: RX11EWReceiverDimmer Instanz

# Verwendung
await device.set_state(0, {"brightness": 128, "on": True})
current_brightness = await device.get_brightness(0)
```

## Migration Timeline

1. **Phase 1** (Aktuell): Neue Architektur parallel zur bestehenden
2. **Phase 2**: Schrittweise Migration bestehender Funktionen
3. **Phase 3**: Vollständige Umstellung auf neue Architektur
4. **Phase 4**: Entfernung der Legacy-Implementierung

Die strukturierte Architektur stellt sicher, dass das ELDAT Plugin wartbar, erweiterbar und zukunftssicher bleibt.