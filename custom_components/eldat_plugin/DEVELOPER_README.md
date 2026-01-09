# ELDAT Plugin - Entwickler-Dokumentation

## Quick Start für Entwickler

### Struktur-Überblick

Das Plugin wurde im Januar 2026 refactored um bessere Wartbarkeit und Erweiterbarkeit zu bieten.

**Wichtigste Änderung**: Die große `base.py` (754 Zeilen) wurde in übersichtliche Module aufgeteilt:

```
transceivers/
  ├── base/          # Abstrakte Basisklassen
  ├── behaviors/     # Wiederverwendbare Mixins
  ├── rx11/          # RX11-spezifische Implementierung
  └── base.py        # DEPRECATED (nur noch Wrapper für Backward Compatibility)
```

### Neue Imports verwenden

**Alt (deprecated aber funktioniert):**
```python
from ....base import BaseReceiver, SwitchBehaviorMixin
```

**Neu (empfohlen):**
```python
from ...base import BaseReceiver, DeviceType, OperatingMode
from ...behaviors import SwitchBehaviorMixin, EntitySpecsMixin
```

### Device-Implementierung

Jede Device-Klasse:
1. Erbt von `BaseReceiver`, `BaseTransmitter` oder `BaseSensor`
2. Kann Mixins für zusätzliche Funktionalität nutzen
3. Muss abstrakte Methoden implementieren

**Beispiel - Einfacher Switch:**

```python
from ...base import BaseReceiver, DeviceType, DeviceSubtype, OperatingMode
from ...behaviors import SwitchBehaviorMixin, EntitySpecsMixin

class RX11SwitchReceiver(SwitchBehaviorMixin, EntitySpecsMixin, BaseReceiver):
    """EW Switch Receiver."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, 
                        device_type=DeviceType.EW_RECEIVER,
                        subtype=DeviceSubtype.SWITCH,
                        **kwargs)
        self._setup_operating_mode(**kwargs)
    
    def _setup_operating_mode(self, **kwargs):
        channels = kwargs.get('channels', 1)
        mode_mapping = {
            1: OperatingMode.SINGLE_CHANNEL,
            2: OperatingMode.DUAL_CHANNEL,
            4: OperatingMode.QUAD_CHANNEL,
        }
        self.operating_mode = mode_mapping.get(channels, OperatingMode.SINGLE_CHANNEL)
    
    @property
    def supported_entity_types(self) -> List[str]:
        return ["button"]  # oder ["switch"]
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        # Verarbeite eingehende Telegramme
        info_type = telegram_data.get("info_type")
        if info_type in [0x03, 0xF1]:  # State change
            self._handle_state_change(telegram_data)
        return self.get_switch_data()
    
    async def set_state(self, channel: int, state: Any) -> bool:
        # Sende Befehl an Gerät
        # Wird vom Mixin verwendet (async_turn_on/off)
        return True
    
    async def get_state(self, channel: int) -> Any:
        # Hole aktuellen Status
        return self.get_switch_state(channel)
```

### Verfügbare Mixins

| Mixin | Verwendung | Methoden |
|-------|------------|----------|
| `SwitchBehaviorMixin` | On/Off Geräte | `async_turn_on()`, `async_turn_off()`, `async_toggle()` |
| `CoverBehaviorMixin` | Rolladen/Jalousien | `async_open_cover()`, `async_close_cover()`, `async_set_cover_position()` |
| `LightBehaviorMixin` | Dimmer | `async_turn_on_light(brightness)`, `async_turn_off_light()` |
| `SensorBehaviorMixin` | Sensoren | `get_temperature()`, `get_humidity()`, `set_sensor_value()` |
| `ButtonBehaviorMixin` | Taster | `register_button_press()`, `get_last_button_press()` |
| `EntitySpecsMixin` | Entity-Generierung | `get_entity_specs()`, `_create_base_entity_spec()` |

### Ordnerstruktur pro Protokoll

```
rx11/devices/
  ├── ew_receivers/           # Einfache EasyWave Empfänger
  │   ├── switch.py
  │   ├── motor.py
  │   └── climate.py
  │
  ├── ew_transmitters/        # EasyWave Sender
  │   ├── single_button.py
  │   ├── dual_button.py
  │   ├── triple_button.py
  │   └── quad_button.py
  │
  ├── ewneo_sensors/          # EasyWave Neo Sensoren
  │   └── ewneo_sensor.py
  │
  └── ewneo_transceivers/     # EasyWave Neo bidirektionale Geräte
      ├── transceiver.py
      ├── switch.py
      ├── dimmer.py
      └── motor.py
```

### Testing

Nach Änderungen:

```bash
# Syntax prüfen
python3 -m py_compile <geänderte_datei.py>

# Plugin neu laden in Home Assistant
# Entwickler-Tools > YAML > Integration neu laden > ELDAT Plugin

# Oder Home Assistant neu starten
```

### Debugging

```python
import logging
_LOGGER = logging.getLogger(__name__)

class MyDevice(BaseReceiver):
    def process_telegram(self, telegram_data):
        _LOGGER.debug("Received telegram: %s", telegram_data)
        # ...
```

Log-Ausgabe in Home Assistant:
- Einstellungen > System > Protokolle
- Oder `/config/home-assistant.log`

### Häufige Fehler

#### 1. Import-Fehler nach Refactoring

**Fehler:**
```
ImportError: cannot import name 'BaseReceiver' from 'custom_components.eldat_plugin.transceivers.base'
```

**Lösung:**
```python
# Statt:
from custom_components.eldat_plugin.transceivers.base import BaseReceiver

# Nutze relative Imports:
from ...base import BaseReceiver
```

#### 2. Mixin Method Resolution Order

**Fehler:**
```python
class MyDevice(BaseReceiver, SwitchBehaviorMixin):  # FALSCH
    pass
```

**Lösung:**
```python
class MyDevice(SwitchBehaviorMixin, BaseReceiver):  # RICHTIG
    pass
```

Mixins müssen vor der Basisklasse kommen!

#### 3. Abstrakte Methoden nicht implementiert

**Fehler:**
```
TypeError: Can't instantiate abstract class MyDevice with abstract methods process_telegram
```

**Lösung:**
```python
class MyDevice(BaseReceiver):
    @property
    def supported_entity_types(self) -> List[str]:
        return ["switch"]  # MUSS implementiert werden
    
    def process_telegram(self, telegram_data: Dict[str, Any]) -> Dict[str, Any]:
        pass  # MUSS implementiert werden
    
    async def set_state(self, channel: int, state: Any) -> bool:
        pass  # MUSS implementiert werden
    
    async def get_state(self, channel: int) -> Any:
        pass  # MUSS implementiert werden
```

### Registry-Integration

Neue Geräte müssen in der Registry registriert werden:

```python
# devices/registry.py

# 1. Import hinzufügen
from .ew_receivers.my_new_device import create_rx11_my_new_device

# 2. In imports Dict eintragen
receiver_imports['my_new_device'] = create_rx11_my_new_device

# 3. Factory-Funktion in der Device-Datei:
def create_rx11_my_new_device(
    serial_number: str,
    name: str = None,
    **kwargs
) -> Optional[RX11MyNewDevice]:
    """Create RX11 MyNewDevice instance."""
    try:
        return RX11MyNewDevice(
            serial_number=serial_number,
            name=name or f"MyNewDevice {serial_number[-6:]}",
            **kwargs
        )
    except Exception as e:
        _LOGGER.error(f"Error creating MyNewDevice: {e}")
        return None
```

### Weitere Dokumentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Detaillierte Architektur-Dokumentation
- [REFACTORING_PLAN.md](../../REFACTORING_PLAN.md) - Refactoring-Historie
- [Inline-Dokumentation](transceivers/base/) - Docstrings in den Klassen

### Hilfe bekommen

1. Prüfen Sie die Beispiele in `ew_receivers/`
2. Lesen Sie die Architektur-Dokumentation
3. Schauen Sie sich die Basisklassen an

---

**Viel Erfolg beim Entwickeln! 🚀**
