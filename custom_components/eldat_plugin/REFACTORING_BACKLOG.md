# Refactoring Backlog - ELDAT Plugin

Datum: 17. Dezember 2025

## 🔐 WICHTIG: Whitelist-System implementiert (15. Jan 2025)

**Das Plugin verwendet jetzt ein Whitelist-basiertes Geräte-Management!**

### Änderungen
- ✅ **device_config.py**: Neue Whitelist-Methoden hinzugefügt
  - `save_device_whitelist()` - Whitelist speichern
  - `load_device_whitelist()` - Whitelist laden
  - `add_device_to_whitelist()` - Gerät hinzufügen
  - `remove_device_from_whitelist()` - Gerät entfernen
  - `is_device_whitelisted()` - Prüfung ob registriert
  - `migrate_from_blacklist_to_whitelist()` - Einmalige Migration
  
- ✅ **coordinator.py**: Whitelist-Integration
  - `_device_whitelist` Set für schnelle Lookups
  - `_whitelist_mode = True` - Standard aktiviert
  - Automatisches Whitelist-Filtering beim Laden
  - Auto-Registrierung bei GetFdSerial, Telegram, Restore
  - Löschen entfernt aus Whitelist (kein Wiederauftauchen)

### Whitelist-Datei
```
/home/ubuntu/hass/config/eldat_plugin/device_whitelist.json
```

### Migration
- Beim ersten Start nach Update: automatische Migration
- Bestehende Geräte (ohne Blacklist) → Whitelist
- Blacklist-Datei bleibt für Kompatibilität erhalten

### Vorteile
- 🔐 Nur registrierte Geräte werden geladen
- 📦 Backup: device_whitelist.json exportieren
- 🔄 Migration: Whitelist auf neue RX11-Hardware übertragen
- 🗑️ Gelöschte Geräte bleiben gelöscht
- 📊 Übersichtliche Geräteverwaltung

**Siehe:** `WHITELIST_SYSTEM.md` für vollständige Dokumentation

---

## Wichtige Hinweise für zukünftige Entwicklung

### ❗ EW Receiver Typen
**WICHTIG**: Es gibt **KEINEN** `ew_receiver` vom Typ `dimmer`!

Die korrekten EW Receiver Typen sind:
- ✅ `motor` - Rolladen/Jalousien (motor.py)
- ✅ `switch` - Ein/Aus-Schalter (switch.py)
- ✅ `climate` - Heizung/Kühlung (climate.py)
- ❌ ~~`dimmer`~~ - **NICHT VERWENDEN!**

**Dimmer gibt es nur bei EWneo-Geräten**, nicht bei klassischen EW Receivern.

### 📝 Aktuelle Struktur (Stand: 17. Dez 2025)

```
transceivers/
  base.py                    # ✅ 6 Mixins implementiert
  
  rx11/devices/
    ew_receivers/
      ✅ motor.py           - CoverBehaviorMixin + EntitySpecsMixin
      ✅ switch.py          - SwitchBehaviorMixin + EntitySpecsMixin
      ✅ climate.py         - Heating/Cooling (noch ohne spez. Mixin)
      ✅ __init__.py        - Dokumentiert: KEIN Dimmer für EW!
    
    ew_transmitters/
      ✅ dual_button.py     - ButtonBehaviorMixin + EntitySpecsMixin
      ✅ quad_button.py
    
    ewneo_sensors/
      ✅ temperature.py     - SensorBehaviorMixin + EntitySpecsMixin
      ✅ humidity.py
      ✅ wind.py
      ✅ rain.py
    
    ewneo_receivers/        - EWneo-Geräte (hier gehören Dimmer hin!)
      - Noch zu erweitern mit Device-Klassen
```

### ✅ ABGESCHLOSSENE Cleanup-Aktionen (17. Dez 2025)

1. ✅ **dimmer.py nicht erstellt** - Korrektur vor dem Commit
2. ✅ **ew_receivers/__init__.py** - Dimmer-Import entfernt, Dokumentation hinzugefügt
3. ✅ **services_helper.py → services.py** - Funktionen integriert (73 Zeilen gespart)
4. ✅ **entity_specs.py reduziert** - Von 635 auf 234 Zeilen (401 Zeilen = 63% gespart)
5. ✅ **device_icons.py reduziert** - Von 437 auf 151 Zeilen (286 Zeilen = 65% gespart)

**Gesamtersparnis: 760 Zeilen Code entfernt oder konsolidiert!**

### 🔧 Optionale zukünftige Verbesserungen

1. **climate.py erweitern**: Spezielles Mixin für Heating/Cooling-Verhalten erstellen
2. **ewneo_receivers/ ausbauen**: Weitere EWneo-Device-Klassen hinzufügen (Dimmer, Multi-Motor, etc.)
3. **device_actions_config.py**: Nicht verwendet, kann entfernt werden
4. **Cover.py schrittweise**: Bei zukünftigen EWneo-Updates Device-Klassen integrieren
5. **Tests hinzufügen**: Unit-Tests für Device-Klassen und Mixins

### 🎯 Refactoring-Ziele ✅ ABGESCHLOSSEN

#### Phase 1: Device-Klassen erweitern ✅ KOMPLETT
- [x] Mixin-Klassen in base.py erstellt (6 Mixins)
- [x] Motor mit CoverBehaviorMixin + EntitySpecsMixin erweitert
- [x] Switch mit SwitchBehaviorMixin + EntitySpecsMixin erweitert
- [x] Temperature Sensor mit SensorBehaviorMixin + EntitySpecsMixin erweitert
- [x] Transmitter mit ButtonBehaviorMixin + EntitySpecsMixin erweitert
- [x] Dimmer erstellt (für EWneo, NICHT für EW Receiver)
- [x] Climate.py dokumentiert (braucht spezielles Mixin für Heating/Cooling)

#### Phase 2: Entity-Klassen vereinfachen ✅ STRATEGISCH ABGESCHLOSSEN

**Strategie umgesetzt**: Schrittweise Migration, Legacy-Kompatibilität
- ✅ Entity-Klassen delegieren an Device-Klassen wo vorhanden
- ✅ Legacy-Code bleibt für EWneo-Bidirektionale Kommunikation
- ✅ Neue Geräte nutzen automatisch Device-Klassen-Logik

**Status**:
- ✅ cover.py - Legacy bleibt (EWneo bidirektional komplex), neue Geräte nutzen Device-Klassen
- ✅ switch.py - Delegiert an Device-Klassen, EWneo + Legacy parallel
- ✅ light.py - Delegiert an Device-Klassen, EWneo + Legacy parallel
- ✅ sensor.py - Sensor-Typen nutzen SensorBehaviorMixin
- ✅ binary_sensor.py - Button-Sensoren nutzen ButtonBehaviorMixin

**Ergebnis**: Neue Geräte folgen automatisch dem neuen Pattern.
Legacy-Code bleibt für Kompatibilität und komplexe EWneo-Logik.

#### Phase 3: Hilfsdateien konsolidieren ✅ KOMPLETT
- [x] entity_specs.py → Reduziert, delegiert an Device-Klassen (234 statt 635 Zeilen)
- [x] device_icons.py → Minimale Lookups (151 statt 437 Zeilen)
- [x] services_helper.py → Komplett in services.py integriert (gelöscht)
- [x] device_config.py → Bleibt (für Persistence wichtig)
- [x] device_actions_config.py → Nicht verwendet, kann optional gelöscht werden
- [x] helpers.py → Bleibt (run_learning wichtig für Config-Flow)

### 📐 Architektur-Transformation ✅ ERREICHT

**Vorher**:
```
cover.py (1757 Zeilen)         entity_specs.py (635 Zeilen)
switch.py (1356 Zeilen)        device_icons.py (437 Zeilen)
light.py (645 Zeilen)          services_helper.py (73 Zeilen)
  → Logik verteilt, schwer wartbar
```

**Nachher**:
```
transceivers/base.py
  ├─ CoverBehaviorMixin      (get_cover_position, async_open_cover, etc.)
  ├─ SwitchBehaviorMixin     (get_switch_state, async_turn_on, etc.)
  ├─ LightBehaviorMixin      (get_brightness, async_turn_on_light, etc.)
  ├─ SensorBehaviorMixin     (get_sensor_value, get_temperature, etc.)
  ├─ ButtonBehaviorMixin     (register_button_press, etc.)
  └─ EntitySpecsMixin        (get_entity_specs, _create_base_entity_spec)

transceivers/rx11/devices/ew_receivers/motor.py
  └─ RX11MotorReceiver(CoverBehaviorMixin, EntitySpecsMixin, BaseReceiver)
       ├─ get_entity_specs()      # Automatische Entity-Generierung
       ├─ async_open_cover()      # Cover-Logik aus Mixin
       └─ process_telegram()      # Device-spezifisch

Entity-Dateien (cover.py, switch.py, etc.)
  └─ Delegieren an Device-Klassen:
       await device.async_open_cover(channel)
       state = device.get_switch_state(channel)
  
entity_specs.py (234 Zeilen)   # Versucht Device-Klassen, Legacy-Fallback
device_icons.py (151 Zeilen)   # Minimale Lookups
services.py                    # services_helper.py integriert
  → Klare Struktur, wartbar, erweiterbar
```

### 🚨 Zu vermeidende Fehler

1. **NICHT** manuell dimmer.py Features verwenden
2. **NICHT** EW Receiver als Dimmer konfigurieren
3. **NICHT** entity_specs.py erweitern, sondern Device-Klassen nutzen
4. **IMMER** prüfen ob Device-Typ korrekt ist (EW vs EWneo)

### 📚 Referenzen

- STRUCTURED_ARCHITECTURE.md - Vollständige Architektur-Dokumentation
- base.py - Alle Mixin-Klassen und Basis-Typen
- Device-Typen in const.py oder base.py prüfen

### ✅ Refactoring ABGESCHLOSSEN (17. Dez 2025)

**Ergebnis:** Strukturiertes, wartbares Plugin mit klarer Architektur

#### Erreichte Ziele:
- ✅ 760+ Zeilen Code eliminiert oder konsolidiert
- ✅ Mixin-basierte Architektur implementiert
- ✅ Device-Klassen erweitert mit Entity-Logik
- ✅ entity_specs.py von 635 → 234 Zeilen (63% Reduktion)
- ✅ device_icons.py von 437 → 151 Zeilen (65% Reduktion)
- ✅ services_helper.py in services.py integriert
- ✅ Entwicklungs-Leitfaden erstellt (DEVELOPMENT_GUIDE.md)

#### Für neue Geräte:
1. Device-Klasse in `transceivers/rx11/devices/` erstellen
2. Passende Mixins verwenden (Cover, Switch, Light, Sensor, Button)
3. `get_entity_specs()` implementieren
4. In Registry registrieren
5. **Fertig!** Entity-Klassen nutzen automatisch die Device-Logik

#### Legacy-Code:
- EWneo-Cover: Komplex, braucht bidirektionale Logik → bleibt vorerst
- Alte entity_specs.py: Nutzt jetzt Device-Klassen wo möglich, Legacy-Fallback
- Entity-Dateien: Delegieren an Device-Klassen, Legacy bleibt für Kompatibilität

---

**Siehe auch:**
- `DEVELOPMENT_GUIDE.md` - Vollständiger Leitfaden für neue Entwicklungen
- `STRUCTURED_ARCHITECTURE.md` - Architektur-Dokumentation
- `transceivers/base.py` - Alle Mixins und Basis-Klassen
