# ELDAT Integration für Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/eldateas/HomeAssistant-Integration.svg)](https://github.com/eldateas/HomeAssistant-Integration/releases)
[![License](https://img.shields.io/github/license/eldateas/HomeAssistant-Integration.svg)](LICENSE)

Eine vollständige Home Assistant Integration für ELDAT EasyWave Geräte über RX11-Transceiver mit umfassender EWneo-Unterstützung.

## ⚠️ Update-Sicherheit

**Ihre eingelernten Geräte sind bei Updates sicher!** 

Das neue DeviceManager-System (ab v2.0) garantiert:
- ✅ **Persistente Speicherung** außerhalb des Plugin-Codes
- ✅ **Automatische Backups** bei jedem Speichern
- ✅ **Versionsmigration** von alten Formaten
- ✅ **Keine Datenverluste** bei Plugin-Updates

→ Siehe [UPGRADE_SAFETY.md](UPGRADE_SAFETY.md) für Details

## Installation

### HACS (empfohlen)

1. Öffne HACS in Home Assistant
2. Klicke auf "Integrationen"
3. Klicke auf die drei Punkte (⋮) oben rechts
4. Wähle "Benutzerdefinierte Repositories"
5. Füge die Repository-URL hinzu: `https://github.com/eldateas/HomeAssistant-Integration`
6. Wähle die Kategorie: `Integration`
7. Klicke auf "Hinzufügen"
8. Suche nach "ELDAT" und installiere die Integration
9. Starte Home Assistant neu

### Manuelle Installation

1. Kopiere den Ordner `custom_components/eldat_plugin` in dein Home Assistant `config/custom_components/` Verzeichnis
2. Stelle sicher, dass GCC installiert ist: `sudo apt-get install build-essential`
3. Die RX11 C-Library wird beim ersten Start automatisch kompiliert
4. Starte Home Assistant neu

### Voraussetzungen

- Home Assistant 2024.1.0 oder höher
- RX11 USB Transceiver (VID: 0x155A, PID: 0x1006 oder 0x1014)
- GCC Compiler (für automatische C-Library Kompilierung)

## Features

### RX11 Transceiver-Unterstützung
- Vollständige Integration des ELDAT RX11 Transceivers
- Native C-Bibliothek Integration für optimale Performance
- Kontinuierliche EWB Receive-Loop für EWneo-Sensoren, EW-Transmitter und EWneo-Receiver
- Automatische Hardware- und Firmware-Versionserkennung

### Erweiterte EWneo-Geräte-Unterstützung
Mit vollständiger 5-Byte EWB_RCV Datenauswertung:

#### **EWneo Schalter (Switch)**
- **Single-Channel**: Ein-Kanal Schalter mit Counter und Betriebsarten (ein/aus/timer/logik)
- **Dual-Channel**: Zwei-Kanal Schalter mit separater Auswertung pro Kanal
- **Quad-Channel**: Vier-Kanal Schalter mit unabhängiger Kanalsteuerung

#### **EWneo Dimmer**
- Vollständige Helligkeitssteuerung (0-255 → 0-100%)
- Smooth-Dimming-Überwachung mit Zeitberechnung (Mantisse × 2^Exponent)
- Echtzeit-Status: Aktueller Level vs. Ziel-Level
- Dimming-Zeit-Restanzeige

#### **EWneo Motoren**
- **Single Motor**: Einzelmotor mit Position/Positionless-Steuerung
- **Dual Motor**: Zweimotorige Steuerung mit unabhängigen Kanälen  
- **Quad Motor**: Viermotorige Steuerung für komplexe Anwendungen
- Automatische Runtime-Kalibrierung-Erkennung
- Terrace-Funktion und Tilt-Control-Unterstützung
- Intelligente Position/Positionless-Modi je nach Kalibrierungsstatus

### Intelligente Steuerungsmodi

#### **Position-Aware Mode** (nach Runtime-Messung)
- Präzise Positionssteuerung (0-100%)
- Zielposition-Tracking während Bewegung
- Gespeicherte Positionen (#1-#3) Unterstützung

#### **Positionless Mode** (ohne Runtime-Messung)  
- Grundlegende Auf/Stop/Zu-Befehle
- Automatische Warnung bei Positionssteuerungs-Versuchen
- Transparenter Fallback-Modus

### Umfassende Zustandsanalyse
- **Motor-Status-Codes**: 117-126 mit vollständiger Interpretation
- **Timer-Modi**: 120s-Timer vs. Runtime-basierte Bewegung
- **Kalibrierungs-Überwachung**: Runtime- und Tilt-Messungen
- **Bewegungstypen**: Öffnen/Schließen (Runtime/Timer/Position)

### Automatische Geräteerkennung
- EWB_RCV Funktionalität für automatisches Anlernen
- Telegram-basierte Geräteerkennung mit 5-Byte Auswertung
- Persistente Geräteverwaltung mit Backup-System

### Entity-Unterstützung
- **Button Entities**: Entfernen-Buttons für alle Geräte  
- **Switch Entities**: Ein/Aus-Steuerung mit 4h-Wiederholung für Heizungssteuerung
- **Light Entities**: Dimmer-Steuerung mit Brightness-Support
- **Cover Entities**: Motor-Steuerung mit Position/Positionless-Modi
- **Sensor Entities**: Temperatur, Luftfeuchtigkeit, Batteriestatus

## Installation

1. Kopieren Sie den `eldat_plugin` Ordner in Ihr `custom_components` Verzeichnis
2. Starten Sie Home Assistant neu  
3. Gehen Sie zu Einstellungen > Integrationen > Integration hinzufügen
4. Suchen Sie nach "ELDAT EasyWave" und folgen Sie dem Setup-Assistenten

## Konfiguration

### RX11 Transceiver Setup
Die Integration erkennt automatisch RX11 Transceiver und initialisiert:
- Hardware-Version (z.B. "ELDATEAS UNS H01") 
- Firmware-Version (z.B. "1.0")
- Kontinuierliche EWB Receive-Loop
- Telegram-Callback-System

### EWneo-Geräte hinzufügen

#### Automatisch (EWB_RCV)
Verwenden Sie den Learn-Service:

```yaml
service: eldat_plugin.learn_device
data:
  device_type: "ewneo_switch"
  timeout: 30
```

#### Manuell mit vollständiger Konfiguration
```yaml
service: eldat_plugin.add_device  
data:
  serial_number: "1234567890ABCDEF"
  device_type: "ewneo_motor"
  device_name: "Wohnzimmer Rollladen"
  operating_mode: 1  # Single Channel
```

## Services

### `eldat_plugin.add_device`
Fügt ein EWneo-Gerät manuell hinzu.

**Parameter:**
- `serial_number`: 16-Byte Seriennummer (Hex)
- `device_type`: EWneo-Gerätetyp (ewneo_switch, ewneo_dimmer, ewneo_motor, etc.)
- `device_name`: Anzeigename (optional)
- `operating_mode`: Betriebsmodus (1=Single, 2=Dual, 4=Quad, optional)

### `eldat_plugin.remove_device`
Entfernt ein Gerät und alle zugehörigen Entities.

**Parameter:**
- `serial_number`: Seriennummer des Geräts
- `force`: Erzwingt Entfernung (optional)

### `eldat_plugin.scan_devices`
Startet Geräte-Scan über RX11 Transceiver.

### `eldat_plugin.learn_device`  
Lernt EWneo-Geräte durch 5-Byte EWB_RCV-Empfang an.

**Parameter:**
- `device_type`: Erwarteter EWneo-Gerätetyp
- `timeout`: Wartezeit in Sekunden (default: 30)

### `eldat_plugin.send_command`
Sendet Befehle an EWneo-Geräte über RX11.

**Parameter:**
- `serial_number`: Zielgerät-Seriennummer
- `command`: Raw-Befehl (Hex) oder
- `button`: Tastennummer (0-3) oder
- `action`: Vordefinierte Aktion (toggle, on, off, up, down, stop)

### Debug-Services
- `eldat_plugin.reset_entity_registry`: Reset Entity-Registry
- `eldat_plugin.cleanup_orphaned_entities`: Aufräumen verwaister Entities
- `eldat_plugin.fix_transceiver`: RX11 Transceiver-Reparatur

## Beispiel Automationen

### EWneo Switch Control
```yaml
automation:
  - alias: "EWneo Schalter Automation"
    trigger:
      platform: state
      entity_id: switch.ewneo_switch_12abcd
    action:
      service: light.toggle
      target:
        entity_id: light.wohnzimmer
```

### EWneo Motor Position Control
```yaml
automation:
  - alias: "Rollladen bei Sonnenstand"
    trigger:
      platform: sun
      event: sunset
    action:
      service: cover.set_cover_position
      target:
        entity_id: cover.ewneo_motor_34abcd  
      data:
        position: 25  # 25% geöffnet
```

### EWneo Dimmer Smooth Control
```yaml
automation:
  - alias: "Smooth Dimming Überwachung"
    trigger:
      platform: state
      entity_id: sensor.ewneo_dimmer_56abcd_dimming_time_remaining
      to: "0"
    action:
      service: notify.mobile_app
      data:
        message: "Dimming abgeschlossen - Helligkeit: {{ states('light.ewneo_dimmer_56abcd') }}"
```

### Heizungssteuerung mit 4h-Wiederholung
```yaml
automation:
  - alias: "Heizung Ein mit Wiederholung"
    trigger:
      platform: numeric_state
      entity_id: sensor.temperature
      below: 18
    action:
      service: switch.turn_on
      target:
        entity_id: switch.ew_receiver_heizung_ein_aus  # Mit 4h-Wiederholung
```

## Entwicklung

### RX11 C-Bibliothek Integration
Die Integration nutzt die ELDAT RX11 C-Bibliothek für optimale Hardware-Kommunikation:

```bash
# Kompilierung der RX11 C-Bibliothek
cd custom_components/eldat_plugin/transceivers/rx11/
chmod +x compile_library.sh
./compile_library.sh
```

### 5-Byte EWB_RCV Protokoll-Implementierung
Vollständige Auswertung der EWneo Telegram-Daten:
- **Byte 0**: Mode (0=Standard für alle EWneo-Geräte)
- **Bytes 1-4**: 32-Bit State-Word (Big-Endian für Motor/Dimmer, Little-Endian für Switches)

### Debug-Modus
Aktivieren Sie umfassendes Logging:

```yaml
logger:
  default: info
  logs:
    custom_components.eldat_plugin: debug
    custom_components.eldat_plugin.coordinator: debug
    custom_components.eldat_plugin.transceivers.rx11: debug
```

### Startup-Optimierungen (v2.1.0)
- Behebt blockierende `_schedule_repeat_timer()` Aufrufe 
- Async-Koroutinen-Verwaltung optimiert
- Bootstrap-Timeout-Probleme gelöst

## Fehlerbehebung

### RX11 Transceiver-Probleme
1. **Hardware-Verbindung prüfen**:
   ```bash
   # Linux: USB-Gerät prüfen
   lsusb | grep -i eldat
   dmesg | grep tty
   ```

2. **C-Bibliothek Kompilierung**:
   - Stellen Sie sicher, dass gcc installiert ist
   - Führen Sie `compile_library.sh` aus
   - Bei Fehlern: Fallback auf Python-Implementierung

3. **Berechtigungen (Linux)**:
   ```bash
   sudo usermod -a -G dialout $USER
   # Neuanmeldung erforderlich
   ```

### EWneo-Geräte werden nicht erkannt
1. **EWB_RCV-Status prüfen**:
   - Integration-Logs auf "EWB Receive-Loop gestartet" prüfen
   - Hardware-/Firmware-Version sollte angezeigt werden

2. **Telegram-Empfang testen**:
   ```yaml
   service: eldat_plugin.scan_devices
   ```

3. **Manuelle Hinzufügung**:
   ```yaml
   service: eldat_plugin.add_device
   data:
     serial_number: "BEKANNTE_SERIENNUMMER"
     device_type: "ewneo_switch"
   ```

### Startup-Blockierung beheben
Falls Home Assistant beim Start hängt:
1. Prüfen Sie die Logs auf "_schedule_repeat_timer" Warnungen
2. Deaktivieren Sie temporär die Integration
3. Aktualisieren Sie auf die neueste Version (Bugfix in v2.1.0)

### Position vs. Positionless-Probleme
**Positionless-Modus** (kein Runtime-Measurement):
- Nur Auf/Stop/Zu-Befehle verfügbar
- Position-Parameter werden abgelehnt
- Log: "Position control not available - runtime measurement required"

**Position-Modus** (mit Runtime-Measurement):
- Vollständige Position-Steuerung verfügbar
- 0-100% Positionierung möglich
- Status-Codes 0-100 zeigen exakte Position

## Technische Details

### Unterstützte EWneo-Protokolle
- **EWneo Switches**: 1/2/4-Kanal mit Counter/Reason-Auswertung
- **EWneo Dimmers**: Smooth-Dimming mit Mantisse/Exponent-Zeitberechnung  
- **EWneo Motors**: Position/Positionless-Modi mit Runtime-Kalibrierung
- **Bidirektionale Kommunikation**: Vollständige Status-Rückmeldung

### RX11 Hardware-Kompatibilität
- ELDAT RX11 Transceiver über C-Bibliothek
- Hardware-Versionserkennung: "ELDATEAS UNS H01"
- Firmware-Versionserkennung: "1.0"
- Kontinuierliche EWB Receive-Loop

### Entity-Status-Attribute
Erweiterte Attribute für alle EWneo-Geräte:
- **mode**: Aktueller Betriebsmodus
- **control_mode**: "position" oder "positionless"
- **supports_position_control**: Boolean
- **is_moving**, **is_calibrating**: Status-Flags
- **dimming_time_remaining**: Verbleibende Smooth-Dimming-Zeit
- **reason_text**: Lesbare Beschreibung der Switch-Gründe

### Integration-Events (Erweitert)
- `eldat_plugin_device_added` (mit EWneo-Typ-Details)
- `eldat_plugin_device_removed`
- `eldat_plugin_device_updated` (bei Status-Änderungen)
- `eldat_plugin_telegram_received` (5-Byte EWB_RCV Daten)
- `eldat_plugin_ewneo_state_changed` (detaillierte State-Änderungen)

## Changelog

### v2.1.0 - EWneo Full Implementation
- ✅ **Startup-Blockierung behoben**: `_schedule_repeat_timer()` Async-Fix
- 🆕 **Vollständige EWneo-Unterstützung**: Switch/Dimmer/Motor (1/2/4-Kanal)
- 🆕 **5-Byte EWB_RCV Parsing**: Komplette State-Auswertung
- 🆕 **Position/Positionless-Modi**: Intelligente Runtime-Kalibrierung-Erkennung
- 🆕 **Smooth-Dimming-Support**: Echtzeit Dimming-Zeit-Überwachung
- 🆕 **Enhanced Logging**: Detailliertes Channel-spezifisches Logging
- 🔧 **C-Library Integration**: RX11 Performance-Optimierung

### v2.0.0 - RX11 Integration  
- 🆕 **RX11 Transceiver-Support**: Native C-Bibliothek Integration
- 🆕 **Kontinuierliche EWB-Loop**: Automatisches Telegram-Processing  
- 🆕 **EWneo-Basis-Implementation**: Grundlegende EWneo-Geräte-Unterstützung

## Lizenz

Diese Integration basiert auf der ELDAT RX11-Bibliothek und unterliegt den entsprechenden Lizenzbestimmungen von ELDAT EaS GmbH.

## Support

Bei Problemen oder Fragen:
1. **Logs prüfen**: Debug-Modus aktivieren für detaillierte Informationen
2. **GitHub Issues**: Erstellen Sie Issues mit Log-Auszügen und Konfiguration  
3. **Hardware testen**: RX11-Verbindung und EWB_RCV-Status prüfen
4. **Version aktualisieren**: Neueste Bugfixes und Features nutzen

### Support-Informationen sammeln
```yaml
# Debug-Info Service aufrufen
service: eldat_plugin.get_debug_info
```

Logs wichtiger Komponenten:
- `custom_components.eldat_plugin.coordinator`
- `custom_components.eldat_plugin.transceivers.rx11.transceiver`  
- `custom_components.eldat_plugin.switch` (für Startup-Probleme)
- `custom_components.eldat_plugin.devices.ewneo_transceivers.*`