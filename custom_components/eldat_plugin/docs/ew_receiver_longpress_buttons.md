# EW-Receiver Long-Press Button Implementation

## Übersicht

Die neue `EWReceiverLongPressButton` Klasse implementiert erweiterte Button-Funktionalität für EW-Receiver mit intelligenter Long-Press-Erkennung:

- **Einzelner Klick**: Nach 1.6 Sekunden ohne weiteren Klick wird ein kurzer Druck ausgeführt
- **Doppelklick (< 1.6s)**: Startet kontinuierliches Senden mit `StartEwSendCmdLoop()`
- **Dritter Klick**: Stoppt kontinuierliches Senden mit `StopEwSendCmdLoop()`

## Implementierungsdetails

### Schwellwert
- `LONG_PRESS_THRESHOLD = 1.6` (Sekunden)

### Button-Verhalten (Neue Logik)

#### Benutzungsverhalten
1. **Kurzer Druck**: Einmal klicken → Warten 1.6s → Kurzer Druck wird ausgeführt
2. **Langer Druck starten**: Einmal klicken → Innerhalb 1.6s nochmals klicken → Kontinuierliches Senden startet
3. **Langer Druck stoppen**: Während kontinuierliches Senden aktiv ist → Klicken → Senden stoppt

#### Technische Umsetzung

**Erster Klick:**
- Startet Timer für 1.6 Sekunden
- Wartet auf möglichen zweiten Klick

**Zweiter Klick (innerhalb 1.6s):**
- Bricht Timer ab
- Startet `StartEwSendCmdLoopRequest()`
- Feuert Event: `press_type: "long_start"`
- Status: `_is_long_press_active = True`

**Klick während aktiver Long-Press:**
- Ruft `StopEwSendCmdLoopRequest()` auf
- Feuert Event: `press_type: "long_end"`
- Resettet Status

**Timer-Ablauf (1.6s ohne zweiten Klick):**
- Führt kurzen Druck aus
- Sendet einzelnen `0x01` (PUSH) Befehl
- Feuert Event: `press_type: "short"`

### Status-Tracking

```python
class EWReceiverLongPressButton:
    def __init__(self):
        self._press_start_time: Optional[float] = None
        self._is_long_press_active = False
        self._long_press_task: Optional[asyncio.Task] = None
```

### Zustandsdiagramm

```
[IDLE] 
   ↓ (Klick)
[WAITING] ← Timer startet (1.6s)
   ↓ (Timer abgelaufen)     ↓ (Zweiter Klick < 1.6s)
[SHORT_EXECUTED]           [LONG_ACTIVE]
   ↓                         ↓ (Klick)
[IDLE]                    [LONG_STOPPED] 
                            ↓
                          [IDLE]
```

### Integration

#### Automatische Verwendung
Alle EW-Receiver Button-Entities verwenden automatisch die neue `EWReceiverLongPressButton` Klasse:

```python
# In button.py - async_setup_entry()
if device_type == "ew_receiver":
    # Verwendet automatisch EWReceiverLongPressButton
    button = EWReceiverLongPressButton(coordinator, serial_number, device_info, entity_spec)
```

#### Entity-Konfiguration
Je nach `action_type` in der Entity-Spezifikation:
- `"press"`: Normaler kurzer Druck
- `"press_and_hold"`: Langer Druck (simuliert 3 Sekunden kontinuierliches Senden)

### Events für Automationen

```yaml
# Automation Beispiel
- alias: "EW-Receiver Button Events"
  trigger:
    - platform: event
      event_type: eldat_button_pressed
  condition:
    - condition: template
      value_template: "{{ trigger.event.data.device_id == 'SERIAL_NUMBER' }}"
  action:
    - choose:
        - conditions:
            - "{{ trigger.event.data.press_type == 'short' }}"
          sequence:
            - service: light.toggle
              target:
                entity_id: light.example
        - conditions:
            - "{{ trigger.event.data.press_type == 'long_start' }}"
          sequence:
            - service: light.turn_on
              target:
                entity_id: light.example
              data:
                brightness: 255
        - conditions:
            - "{{ trigger.event.data.press_type == 'long_end' }}"
          sequence:
            - service: light.turn_off
              target:
                entity_id: light.example
```

### Command-Hierarchie

Die Implementierung versucht in folgender Reihenfolge:

1. **Coordinator's eldat_wrapper**: `coordinator.eldat_wrapper.start_continuous_command()`
2. **RX11 Wrapper**: `transceiver._rx11_wrapper` mit direkten C-Library Calls
3. **Transceiver Methods**: Fallback zu `_start_continuous_sending()`
4. **Simple Commands**: Normale PUSH/RELEASE Befehle als letzter Ausweg

### Logging

Alle Actions werden detailliert geloggt:
- `🔘` Button pressed
- `👆` Short press executed
- `🔒` Long press detected
- `🚀` Continuous sending started (StartEwSendCmdLoop)
- `🛑` Continuous sending stopped (StopEwSendCmdLoop)

### Kompatibilität

- **Rückwärtskompatibel**: Bestehende Button-Entities funktionieren weiterhin
- **Automatische Migration**: EW-Receiver verwenden automatisch die neue Klasse
- **Fallback-Mechanismen**: Bei fehlenden C-Library Funktionen wird auf Standard-Befehle zurückgegriffen

## Verwendung

### Direkte Button-Verwendung
```python
# Button drücken in Home Assistant UI
# - Kurzer Klick = kurzer Druck
# - Konfigurierte Long-Press Buttons = langer Druck
```

### Service Calls
```yaml
# Kurzer Druck
service: button.press
target:
  entity_id: button.ew_receiver_0_toggle

# Langer Druck (bei entsprechend konfigurierten Buttons)
service: button.press
target:
  entity_id: button.ew_receiver_0_toggle_hold
```

## Fehlerbehebung

### Debug Logging
```yaml
# In configuration.yaml
logger:
  logs:
    custom_components.eldat_plugin.button: debug
```

### Häufige Probleme
1. **StartEwSendCmdLoop nicht verfügbar**: Fallback auf normale Befehle
2. **Wrapper nicht verbunden**: Prüfung der Transceiver-Verbindung
3. **Event nicht gefeuert**: Prüfung der Entity-Konfiguration

## Technische Details

### Implementierte Klassen
- `EWReceiverLongPressButton`: Haupt-Implementierung mit Timer-basierter Erkennung
- Erweiterte `async_press()` Methode mit Short/Long-Press Unterscheidung
- Integrierte Event-Feuermechanismen

### Timeout-Behandlung
- Automatische Cleanup bei Fehlern
- Graceful Fallback bei Missing-Library-Funktionen
- Exception-safe Implementation
