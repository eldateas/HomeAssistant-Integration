# EW-Receiver UI-freundliche Button-Implementierung (mit Toggle Long-Press)

## Übersicht

Das neue UI-freundliche Button-System erstellt für jeden EW-Receiver automatisch **zwei separate Button-Entitäten** in der Home Assistant-Benutzeroberfläche:
- **Kurzer Druck-Button**: Führt eine einmalige Aktion aus
- **Langer Druck-Button**: **Schiebeschalter** - Ein Klick startet kontinuierliches Senden, zweiter Klick stoppt es

## Funktionsweise

### Automatische Button-Erstellung

Für jeden EW-Receiver-Button mit `supports_long_press: True` werden automatisch erstellt:

1. **`[ButtonName] (Kurz)`** - Icon: `mdi:gesture-tap`
   - Sendet einmaligen Befehl
   - Typische Verwendung: Ein/Aus-Schalten, kurze Befehle

2. **`[ButtonName] (Lang)`** - Icon: `mdi:gesture-tap-hold` / `mdi:stop-circle`
   - **Schiebeschalter-Funktion:**
     - **1. Klick:** Startet kontinuierliches Senden (Icon wird zu Stop)
     - **2. Klick:** Stoppt kontinuierliches Senden (Icon wird zu Hold)
   - Typische Verwendung: Dimmen, Rollladen-Bewegung

### Toggle-Funktion (Schiebeschalter)

Die Long-Press Buttons funktionieren als **echte Schiebeschalter**:

```
Zustand: AUS  →  [Klick]  →  Zustand: EIN   →  [Klick]  →  Zustand: AUS
         ⏸️              →           ⏯️              →           ⏸️
    Nicht aktiv          →    Kontinuierlich       →      Nicht aktiv
                         →       sendend            →
```

### Beispiel-Buttons

Bei einem **Dimmer** im 2-Tasten-Modus werden erstellt:
- `Heller (Kurz)` - Ein Dimm-Schritt heller
- `Heller (Lang)` - **Toggle:** Start/Stop kontinuierlich heller dimmen
- `Dunkler (Kurz)` - Ein Dimm-Schritt dunkler  
- `Dunkler (Lang)` - **Toggle:** Start/Stop kontinuierlich dunkler dimmen

Bei einem **Schalter** im 1-Tasten-Modus werden erstellt:
- `Toggle (Kurz)` - Einmaliges Umschalten
- `Toggle (Lang)` - **Toggle:** Start/Stop kontinuierliches Senden

### Icon-Verhalten

**Long-Press Buttons ändern ihr Icon dynamisch:**
- **Inaktiv:** `mdi:gesture-tap-hold` (Hand mit Finger)
- **Aktiv:** `mdi:stop-circle` (Stop-Kreis)

**Benutzer sehen sofort den aktuellen Zustand!**

## Konfiguration

### Entity-Spezifikation
```python
{
    "type": "button",
    "channel": 0,
    "name": "Heller",
    "action": "brighter",
    "supports_long_press": True,  # Aktiviert UI-Trennung
    "action_type": "press",       # Basis-Button-Typ
    "long_press_duration": 2.5    # Dauer für Long-Press in Sekunden
}
```

### Automatische Generierung
Das System erkennt automatisch `supports_long_press: True` und erstellt:
1. **Short-Press Button**: `action_type: "press"`
2. **Long-Press Button**: `action_type: "press_and_hold"`

## Events

Both Button-Typen feuern `eldat_button_pressed` Events für Automatisierungen:

```yaml
# Beispiel-Event für kurzen Druck
event_type: eldat_button_pressed
data:
  device_id: "90EE931E"
  entity_id: "button.ew_receiver_heller_kurz"
  press_type: "short"
  action: "brighter"
  action_type: "press"
  channel: 0
  receiver_kind: "dimmer"
  device_name: "EW-Receiver 90EE931E"

# Beispiel-Event für langen Druck START (Toggle EIN)
event_type: eldat_button_pressed
data:
  device_id: "90EE931E"
  entity_id: "button.ew_receiver_heller_lang"
  press_type: "long_start"
  action: "brighter"
  action_type: "press_and_hold"
  channel: 0
  receiver_kind: "dimmer"
  device_name: "EW-Receiver 90EE931E"
  is_active: true

# Beispiel-Event für langen Druck STOP (Toggle AUS)
event_type: eldat_button_pressed
data:
  device_id: "90EE931E"
  entity_id: "button.ew_receiver_heller_lang"
  press_type: "long_stop"
  action: "brighter"
  action_type: "press_and_hold"
  channel: 0
  receiver_kind: "dimmer"
  device_name: "EW-Receiver 90EE931E"
  is_active: false
```

## Implementierung

### EWReceiverUIButton Klasse
```python
class EWReceiverUIButton(EldatEntity, ButtonEntity):
    """EW-Receiver button optimized for Home Assistant UI with toggle long press actions."""
    
    def __init__(self, ...):
        # Toggle state for long press (Schiebeschalter)
        self._is_long_press_active = False
        self._continuous_task: Optional[asyncio.Task] = None
    
    async def async_press(self) -> None:
        if self._action_type == "press_and_hold":
            # Toggle long press action (Schiebeschalter)
            if self._is_long_press_active:
                await self._stop_long_press()  # Toggle AUS
            else:
                await self._start_long_press()  # Toggle EIN
        else:
            await self._execute_short_press()  # Einmaliger Befehl
```

### Toggle Long-Press Verhalten
1. **Start (`_start_long_press()`):**
   - Startet kontinuierliche Sende-Schleife (alle 500ms)
   - Setzt `_is_long_press_active = True`
   - Icon wird zu `mdi:stop-circle`
   - Event: `press_type: "long_start"`

2. **Stop (`_stop_long_press()`):**
   - Stoppt kontinuierliche Sende-Schleife
   - Setzt `_is_long_press_active = False` 
   - Icon wird zu `mdi:gesture-tap-hold`
   - Event: `press_type: "long_stop"`

3. **Kontinuierliche Schleife (`_continuous_sending_loop()`):**
   - Sendet alle 500ms den gleichen Befehl
   - Läuft bis zum Stop oder Fehler
   - Robust gegen Verbindungsfehler

## Vorteile

✅ **UI-freundlich**: Klare Trennung zwischen kurzen und langen Aktionen
✅ **Schiebeschalter-Logik**: Long-Press Buttons funktionieren als Toggle (Ein/Aus)
✅ **Visuelle Rückmeldung**: Icon ändert sich je nach Zustand (Hold/Stop)
✅ **Benutzerfreundlich**: Explizite Button-Namen eliminieren Verwirrung
✅ **Automatisierung**: Events für Start/Stop ermöglichen erweiterte Automatisierungen
✅ **Flexibel**: Kontinuierliches Senden mit manueller Kontrolle
✅ **Robust**: Umfangreiche Fehlerbehandlung und automatisches Cleanup
✅ **Echte Kontrolle**: Benutzer bestimmt Dauer durch manuelles Stoppen

## Migration

Das neue System ist vollständig rückwärtskompatibel:
- Bestehende `EWReceiverLongPressButton` funktioniert weiter für spezielle Fälle
- Neue `EWReceiverUIButton` wird für alle UI-optimierten Buttons verwendet
- Automatische Erkennung basierend auf `supports_long_press` Flag

## Service Calls

```yaml
# Kurzer Druck
service: button.press
target:
  entity_id: button.ew_receiver_heller_kurz

# Langer Druck (Toggle)
# Erster Aufruf: Startet kontinuierliches Senden
# Zweiter Aufruf: Stoppt kontinuierliches Senden
service: button.press
target:
  entity_id: button.ew_receiver_heller_lang
```

### Automatisierung Beispiele

```yaml
# Automatik: Dimmen für 5 Sekunden, dann stoppen
automation:
  - alias: "Auto-Dimm-Timer"
    trigger:
      - platform: event
        event_type: eldat_button_pressed
        event_data:
          press_type: "long_start"
          action: "brighter"
    action:
      - delay: '00:00:05'  # 5 Sekunden warten
      - service: button.press
        target:
          entity_id: "{{ trigger.event.data.entity_id }}"  # Gleichen Button stoppen

# Automatik: Bei Start anderen Kanal stoppen (exklusiv)
automation:
  - alias: "Exklusiv-Dimmen"
    trigger:
      - platform: event
        event_type: eldat_button_pressed
        event_data:
          press_type: "long_start"
    action:
      # Stoppe anderen Dimm-Kanal
      - service: button.press
        target:
          entity_id: >
            {% if trigger.event.data.action == 'brighter' %}
              button.ew_receiver_dunkler_lang
            {% else %}
              button.ew_receiver_heller_lang
            {% endif %}
```