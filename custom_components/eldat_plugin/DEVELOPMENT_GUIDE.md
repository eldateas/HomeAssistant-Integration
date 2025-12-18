# ELDAT Plugin - Entwicklungs-Leitfaden

## Neue Geräte implementieren

### 1. Device-Klasse erstellen

**Beispiel**: Neuer EWneo Quad Switch

```python
# transceivers/rx11/devices/ewneo_receivers/quad_switch.py

from ....base import (
    BaseReceiver,
    DeviceType,
    DeviceSubtype,
    OperatingMode,
    SwitchBehaviorMixin,
    EntitySpecsMixin,
)

class RX11EWneoQuadSwitch(SwitchBehaviorMixin, EntitySpecsMixin, BaseReceiver):
    """EWneo Quad Switch with 4 independent channels."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            device_type=DeviceType.EWNEO_QUAD_SWITCH,
            subtype=DeviceSubtype.QUAD_SWITCH,
            operating_mode=OperatingMode.QUAD_CHANNEL,
            **kwargs
        )
    
    @property
    def supported_entity_types(self) -> List[str]:
        return ["switch"]
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications - wird automatisch genutzt!"""
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Create switch entity for each channel
        for channel in range(4):
            specs["switch"].append(self._create_base_entity_spec(
                "switch",
                channel=channel,
                name=f"{self.name} CH{channel+1}",
                device_class="switch",
                icon="mdi:light-switch"
            ))
        
        return specs
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming telegram."""
        # Handle state changes
        channel = telegram_data.get("channel", 0)
        state = telegram_data.get("state")
        
        if state is not None:
            self.set_switch_state(channel, bool(state))  # Nutzt Mixin!
        
        return self.get_state()
    
    async def set_state(self, channel: int, state: bool) -> bool:
        """Set switch state - implementiert Command-Sending."""
        # TODO: Send actual EWB command
        self.set_switch_state(channel, state)  # Update internal state
        return True
    
    async def get_state(self, channel: int) -> bool:
        """Get current state."""
        return self.get_switch_state(channel)  # Aus Mixin
```

### 2. Device in Registry registrieren

```python
# transceivers/rx11/devices/registry.py

from .ewneo_receivers.quad_switch import RX11EWneoQuadSwitch

DEVICE_TYPE_MAPPING = {
    # ... existing mappings ...
    (DeviceType.EWNEO_QUAD_SWITCH, None): RX11EWneoQuadSwitch,
}
```

### 3. Entity-Klasse nutzt Device automatisch

Die Entity-Klasse in `switch.py` delegiert bereits an Device-Klassen:

```python
# In switch.py ist bereits implementiert:

async def async_turn_on(self, **kwargs: Any) -> None:
    """Turn on the switch."""
    device_instance = self.coordinator.device_registry.get_device_instance(self._serial_number)
    if device_instance and hasattr(device_instance, 'async_turn_on'):
        await device_instance.async_turn_on(self._channel)
```

**Das wars!** Die Entity-Klasse findet automatisch die Device-Klasse und nutzt deren Methoden.

## Vorteile der neuen Architektur

### ✅ Was die Mixins bieten

#### CoverBehaviorMixin
- `get_cover_position(channel)` / `set_cover_position(channel, pos)`
- `get_cover_state(channel)` / `set_cover_state(channel, state)`
- `async_open_cover(channel)` / `async_close_cover(channel)`
- `async_set_cover_position(channel, position)`
- `supports_position` / `supports_tilt` Properties

#### SwitchBehaviorMixin
- `get_switch_state(channel)` / `set_switch_state(channel, state)`
- `async_turn_on(channel)` / `async_turn_off(channel)`
- `async_toggle(channel)`

#### LightBehaviorMixin
- `get_light_state(channel)` / `set_light_state(channel, state)`
- `get_brightness(channel)` / `set_brightness(channel, brightness)`
- `async_turn_on_light(channel, brightness)`
- `async_turn_off_light(channel)`
- `supports_brightness` Property

#### SensorBehaviorMixin
- `get_sensor_value(sensor_type)`
- `set_sensor_value(sensor_type, value)`
- `get_temperature()` / `get_humidity()` / etc.

#### EntitySpecsMixin
- `get_entity_specs()` - Generiert automatisch Entity-Spezifikationen
- `_create_base_entity_spec()` - Helper für Entity-Specs

### ✅ Was entity_specs.py jetzt macht

Die neue schlanke `entity_specs.py` (234 Zeilen statt 635):

1. **Versucht Device-Klasse zu nutzen**:
   ```python
   device_class = get_device_class_for_info(device_info)
   if device_class and hasattr(device_class, 'get_entity_specs'):
       specs = device_class().get_entity_specs()  # ✅ Nutzt Device!
   ```

2. **Fällt auf Legacy zurück** wenn Device-Klasse nicht existiert
3. **Ist nur noch ein Wrapper** - alle neue Logik geht in Device-Klassen

## Migration bestehender Geräte

### Prioritäten

1. **Neue EWneo Geräte** → Sollten Device-Klassen nutzen
2. **Einfache EW Receiver** (Switch, Motor) → Können migriert werden
3. **Komplexe EWneo Cover** → Später, brauchen bidirektionale Logik

### Beispiel-Migration: EW Switch

**Vorher** (in switch.py):
```python
class EldatConfiguredSwitch(EldatEntity, SwitchEntity):
    def __init__(...):
        # 50+ Zeilen Initialisierung
        self._is_on = False
        self._receiver_kind = ...
        # Viel Zustandsverwaltung
    
    async def async_turn_on(self, **kwargs):
        # 30+ Zeilen Logik
        # Command-Building
        # State-Updates
        # Error-Handling
```

**Nachher** (Device-Klasse macht die Arbeit):
```python
class EldatConfiguredSwitch(EldatEntity, SwitchEntity):
    def __init__(...):
        super().__init__(...)
        self._device = coordinator.device_registry.get_device_instance(serial_number)
    
    async def async_turn_on(self, **kwargs):
        if self._device:
            await self._device.async_turn_on(self._channel)  # ✅ Delegiert!
```

## Tests schreiben

### Device-Klasse testen

```python
import pytest
from custom_components.eldat_plugin.transceivers.rx11.devices.ew_receivers.switch import RX11SwitchReceiver

async def test_switch_turn_on():
    device = RX11SwitchReceiver(
        serial_number="1234567890ABCDEF",
        name="Test Switch"
    )
    
    await device.async_turn_on(channel=0)
    assert device.get_switch_state(0) == True
    
async def test_entity_specs_generation():
    device = RX11SwitchReceiver(
        serial_number="1234567890ABCDEF",
        name="Test Switch"
    )
    
    specs = device.get_entity_specs()
    assert "switch" in specs
    assert len(specs["switch"]) == 1
    assert specs["switch"][0]["type"] == "switch"
```

## Best Practices

### DO ✅
- Neue Geräte als Device-Klassen in `transceivers/rx11/devices/` implementieren
- Passende Mixins für Verhalten nutzen
- `get_entity_specs()` implementieren für automatische Entity-Generierung
- State in Mixin-Properties speichern (`set_switch_state()`, etc.)
- Legacy-Code für Kompatibilität behalten

### DON'T ❌
- Neue Logik in `entity_specs.py` oder `device_icons.py` hinzufügen
- Entity-Klassen mit Business-Logik überladen
- Bestehende EWneo-Cover ohne Tests anfassen (komplex!)
- Mixins ohne `super().__init__()` aufrufen

## Dateistruktur-Übersicht

```
custom_components/eldat_plugin/
├── transceivers/
│   ├── base.py                    # ✅ MIXINS HIER!
│   └── rx11/
│       ├── devices/
│       │   ├── registry.py        # Device-Type → Klasse Mapping
│       │   ├── ew_receivers/
│       │   │   ├── switch.py      # ✅ Device-Klassen
│       │   │   ├── motor.py       # ✅ Mit Mixins
│       │   │   └── climate.py
│       │   ├── ew_transmitters/
│       │   │   └── dual_button.py # ✅ ButtonBehaviorMixin
│       │   └── ewneo_sensors/
│       │       └── temperature.py  # ✅ SensorBehaviorMixin
│       └── transceiver.py
├── cover.py                       # Entity-Klassen (delegieren)
├── switch.py                      # Entity-Klassen (delegieren)
├── light.py                       # Entity-Klassen (delegieren)
├── sensor.py                      # Entity-Klassen (delegieren)
├── entity_specs.py                # ⚠️ LEGACY - nutzt Device-Klassen
└── device_icons.py                # ⚠️ LEGACY - minimale Lookups
```

## Zusammenfassung

**Die neue Architektur ist bereits aktiv!**

- ✅ Mixins in `base.py` definiert
- ✅ Device-Klassen nutzen Mixins
- ✅ `entity_specs.py` priorisiert Device-Klassen
- ✅ Legacy-Code bleibt für Kompatibilität
- 🔄 Neue Geräte folgen automatisch dem neuen Pattern

**Nächste Schritte für Entwickler:**
1. Neue Geräte als Device-Klassen implementieren
2. Passende Mixins auswählen und kombinieren
3. `get_entity_specs()` implementieren
4. In Registry registrieren
5. Fertig! Entity-Klassen nutzen automatisch die Device-Logik
