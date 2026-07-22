# Easywave Integration – Anwender-Dokumentation

> **Version:** 0.7.0 | **Letzte Aktualisierung:** Juli 2026

> **Hinweis (0.7.0):** Geräte liegen in Config-Subentries (CORE-kompatibel). Nach dem Upgrade von 0.6.x Automationen/Device-Trigger neu anlegen. Siehe [CHANGELOG.md](../CHANGELOG.md).

EN **[English Version](index.md)**

Diese Dokumentation beschreibt die Einrichtung und Nutzung der Easywave Integration für Home Assistant (z.B. **Home Assistant Green**) mit dem RX11 USB-Transceiver.

---

## Inhaltsverzeichnis

1. [Überblick](#überblick)
2. [Hardware-Voraussetzungen](#hardware-voraussetzungen)
3. [Installation](#installation)
4. [RX11 Transceiver einrichten](#rx11-transceiver-einrichten)
5. [Unterstützte Gerätetypen](#unterstützte-gerätetypen)
6. [Geräte einlernen](#geräte-einlernen)(#geräteverhalten-und-funktionen)
7. [Entity-Typen](#entity-typen)
8. [Services](#services)
9. [Automationen](#automationen)
10. [Fehlerbehebung](#fehlerbehebung)

---

## Überblick

Die Easywave Integration ermöglicht die Steuerung und Überwachung von ELDAT Funkgeräten über Home Assistant. Die Kommunikation erfolgt über den **RX11 USB-Transceiver**, der an deinen **Home Assistant Green** (oder anderen Home Assistant Host) angeschlossen wird.

### Funktionsprinzip des RX11 Transceivers

```
┌─────────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│                     │   USB   │                  │   868   │                 │
│  Home Assistant     │◄───────►│  RX11 Transceiver│◄ ─ ─ ─ ►│  Easywave/EWneo │
│  (Green)            │         │                  │   MHz   │     Geräte      │
└─────────────────────┘         └──────────────────┘         └─────────────────┘
```

**Der RX11 Transceiver:**
- Empfängt Tastencodes von Easywave-Sendern (Fernbedienungen, Wandtaster)
- Sendet Tastencodes an Easywave-Empfänger (Schalter, Motoren)
- Kommuniziert bidirektional mit EWneo-Aktoren (Statusrückmeldung)
- Empfängt Sensordaten von EWneo-Sensoren
- Läuft im kontinuierlichen Empfangsmodus

### Kommunikationsverhalten der Gerätetypen

| Gerätetyp | Kommunikation | Beschreibung |
|-----------|---------------|-------------|
| **Easywave-Sender** | Senden → RX11 | Senden Tastencodes bei Betätigung. Totmann-Betrieb möglich (Drücken & Loslassen erkennbar) |
| **Easywave-Empfänger** | RX11 → Empfangen | Empfangen Tastencodes vom RX11. **Kein Zustand wird zurückgesendet** – der aktuelle Schaltzustand ist in Home Assistant nicht bekannt |
| **EWneo-Aktoren** | RX11 ↔ Bidirektional | Vollständige Zwei-Wege-Kommunikation. Aktoren melden ihren aktuellen Zustand zurück (Position, Ein/Aus, etc.) |
| **EWneo-Sensoren** | Senden → RX11 | Senden periodisch Messwerte (Temperatur, Feuchte, etc.). Keine bidirektionale Kommunikation |

---

## Hardware-Voraussetzungen

| Komponente | Anforderung |
|------------|-------------|
| **Home Assistant** | Version 2026.3.0 oder höher |
| **Hardware** | Home Assistant Green, Yellow, oder anderer Linux-basierter Host |
| **RX11 USB-Transceiver** | VID: 0x155A, PID: 0x1014 |

### RX11 Hardware-Identifikation

```bash
# USB-Gerät prüfen
lsusb | grep 155a

# Erwartete Ausgabe:
# Bus xxx Device xxx: ID 155a:1014 ELDAT GmbH RX11 USB Transceiver
```

---

## Installation

### HACS Installation (empfohlen)

1. Öffne **HACS** in Home Assistant
2. Navigiere zu **Integrationen**
3. Klicke auf **⋮** (drei Punkte) → **Benutzerdefinierte Repositories**
4. Füge hinzu: `https://github.com/eldateas/HomeAssistant-Integration`
5. Kategorie: `Integration`
6. Suche nach **"Easywave"** und installiere
7. **Home Assistant neu starten**

### Manuelle Installation

```bash
# In das config-Verzeichnis wechseln
cd /config/custom_components/

# Repository klonen oder Dateien kopieren
```

---

## RX11 Transceiver einrichten

### Ersteinrichtung

1. **RX11 anschließen** – Verbinde den USB-Transceiver mit einem USB-Port deines Home Assistant Green
2. **Integration hinzufügen** – *Einstellungen → Geräte & Dienste → Integration hinzufügen → "Easywave"*
3. **Automatische Erkennung** – Der RX11 wird automatisch erkannt und angezeigt
4. **Auswahl bestätigen** – Wähle den erkannten Transceiver aus

### Nach der Einrichtung

Nach erfolgreicher Einrichtung wird der RX11 als Gerät in Home Assistant angezeigt mit:
- **Hardware-Version** (z.B. "ELDATEAS UNS H01")
- **Firmware-Version** (z.B. "1.0")
- **Verbindungsstatus**

---

## Unterstützte Gerätetypen

### Easywave-Sender (unidirektional → RX11)

| Gerätetyp | Beschreibung | Kommunikation |
|-----------|--------------|---------------|
| `ew_transmitter` | Fernbedienungen, Wandtaster, Handsender | Senden Tastencodes (A, B, C, D) an den RX11. **Totmann-Empfang möglich:** Drücken und Loslassen werden separat erkannt |

> 💡 **Hinweis:** Easywave-Sender können sowohl als Impuls-Schalter (kurzer Druck) als auch im Totmann-Modus (solange gedrückt) verwendet werden.

### Easywave-Empfänger (RX11 → unidirektional)

| Gerätetyp | Beschreibung | Kommunikation |
|-----------|--------------|---------------|
| `ew_receiver` | Schalter, Motoren, Dimmer, Heizungsaktoren | Empfangen Tastencodes vom RX11. **Kein Zustand wird zurückgesendet** |

> ⚠️ **Wichtig:** Easywave-Empfänger senden **keinen Zustand zurück**. Home Assistant kennt den aktuellen Schaltzustand nicht und kann ihn nur "annehmen". Bei manueller Bedienung am Gerät kann der Zustand in Home Assistant abweichen.

### EWneo-Aktoren (bidirektional ↔ RX11)

| Gerätetyp | Code | Beschreibung |
|-----------|------|--------------|
| `ewneo_switch` | 0x03 | Ein-Kanal Schalter mit Statusrückmeldung |
| `ewneo_motor` | 0x05 | Motor/Rollladen mit Positionsrückmeldung |
| `ewneo_dual_switch` | 0x06 | Zwei-Kanal Schalter |
| `ewneo_quad_switch` | 0x07 | Vier-Kanal Schalter |
| `ewneo_dual_motor` | 0x08 | Zwei-Kanal Motor |
| `ewneo_quad_motor` | 0x09 | Vier-Kanal Motor |

> ✅ **Vorteil:** EWneo-Aktoren kommunizieren **bidirektional**. Der aktuelle Gerätezustand (Ein/Aus, Position, etc.) wird an Home Assistant zurückgemeldet – auch bei manueller Bedienung.

### EWneo-Sensoren (unidirektional → RX11)

| Sensortyp | Messwerte | Kommunikation |
|-----------|----------|---------------|
| **Temperatursensor** | Temperatur (°C) | Periodisches Senden |
| **Temperatur / Feuchtigkeitssensor** | Temperatur, Luftfeuchtigkeit | Periodisches Senden |

> 📡 **Hinweis:** EWneo-Sensoren senden ihre Messwerte periodisch an den RX11. Sie empfangen keine Befehle (keine bidirektionale Kommunikation).

---

## Geräte einlernen

### Easywave-Sender einlernen

**Anwendung:** Fernbedienungen, Wandtaster, Handsender

1. Im Geräte-Dialog **"Easywave-Sender"** auswählen
2. **Bedienart** wählen:
   - **1-Tastbedienung:** Eine Taste pro Funktion (z.B. Toggle)
   - **2-Tastbedienung:** Zwei Tasten pro Funktion (z.B. Ein/Aus oder Auf/Zu)
   - **3-Tastbedienung:** Drei Tasten pro Funktion (z.B. Auf/Stopp/Zu)
3. **Bedienung** wählen (nur bei 1-Tastbedienung):
   - **Einzeln:** Jede Taste erzeugt ein eigenes Objekt (jede Taste bildet eigenen Zustand ab)
   - **Gruppe:** Alle Tasten erzeugen ein gemeinsames Objekt (Tasten wechseln den Zustand)
4. **Betriebsart** konfigurieren (nur bei 1-Tastbedienung):
   - **Impuls:** Zustand fällt nach Loslassen auf "nicht betätigt" zurück
   - **Dauer:** Der letzte Zustand (betätigt/nicht betätigt) wird beibehalten
5. **Tastenanzahl** wählen (nur bei 1-Tastbedienung) – Anzahl der Tasten des Senders (1, 2, 3 oder 4)
6. **Sendertaste betätigen** – Drücke die einzulernende Taste
7. Einlernen bestätigen

**Bedienarten im Detail:**

| Bedienart | Tasten | Beschreibung | Typische Nutzung |
|-----------|--------|--------------|------------------|
| 1-Tast | 1 | Eine Taste wechselt den Zustand (Toggle) | Lichtschalter, Klingel |
| 2-Tast | 2 | Taste A = Ein/Auf, Taste B = Aus/Zu | Rolladenschalter, Lichtschalter |
| 3-Tast | 3 | Taste A = Auf, Taste B = Stopp, Taste C = Zu | Rollladensteuerung |

**Bedienung (nur 1-Tastbedienung):**

| Option | Objekte | Beschreibung | Typische Nutzung |
|--------|---------|--------------|------------------|
| Einzeln | Pro Taste | Jede Taste wird als separates Objekt dargestellt | Mehrere unabhängige Schalter |
| Gruppe | Ein Objekt | Alle Tasten steuern ein gemeinsames Objekt | Ein Gerät mit mehreren Tasten |

**Betriebsarten (nur 1-Tastbedienung):**

| Option | Beschreibung | Typische Nutzung |
|--------|--------------|------------------|
| Impuls | Zustand wechselt bei Druck, fällt nach Loslassen zurück | Klingeltaster, Türöffner |
| Dauer | Zustand bleibt nach Tastendruck erhalten | Lichtschalter |

### Easywave-Empfänger einlernen

**Anwendung:** Schalter, Relais, Heizungsaktoren

1. **"Easywave-Empfänger"** im Dialog wählen
2. **Betriebsart** auswählen:
   - **IMPULS (1-Tast):** Kurzer Schaltimpuls von 1s
   - **EIN/AUS (2-Tast):** Relaisausgang gezielt EIN oder AUS schalten
   - **AUF/ZU (2-Tast):** Relaisausgang gezielt AUF oder ZU schalten
   - **AUF/STOPP/ZU (3-Tast):** Befehle für Rohrmotoren (Auf/Stopp/Zu)
   - **EIN/AUS (Heizung):** EIN/AUS mit 4h-Wiederholung des letzten Zustands
   - **UNIVERSAL (4-Tast):** Schaltimpulse für alle 4 Sendecodes
3. **Empfänger in Programmiermodus versetzen** (am Gerät, passend zur gewählten Betriebsart)
4. **Lernbefehl senden** bestätigen
5. Erfolgreiche Kopplung bestätigen

### Easywave neo Empfänger einlernen (bidirektional)

**Anwendung:** Moderne Easywave neo Aktoren mit bidirektionaler Kommunikation und Statusrückmeldung

1. **"Easywave neo Empfänger"** im Dialog wählen
2. **Empfänger in Programmiermodus versetzen:**
   - Bringen Sie den Empfänger in den Programmiermodus einer beliebigen Betriebsart
   - Die LED am Empfänger sollte blinken
3. **"Weiter"** klicken – Der RX11 sendet eine Kopplungsanfrage
4. **Warten auf Bestätigung** – Das Gerät antwortet mit seinen Gerätedaten
5. **Gerätename vergeben** und optional einen Bereich zuweisen
6. Einlernen bestätigen

> ✅ **Vorteil:** Easywave neo Empfänger werden automatisch erkannt. Der Gerätetyp (Schalter, Motor, Dimmer) und die Kanalanzahl werden vom Gerät selbst übermittelt.

### Easywave neo Sensoren einlernen

**Anwendung:** Temperatur-, Feuchte- und Klimasensoren

1. **"Easywave neo Sensor"** im Dialog wählen
2. **"Sensor einlernen"** klicken
3. **Lerntaste am Sensor betätigen** – Drücken Sie die Lerntaste auf der Rückseite des Sensors
4. **Warten auf Empfang** – Home Assistant erkennt automatisch die verfügbaren Messdaten
5. **Gerätename vergeben** und optional einen Bereich zuweisen
6. Einlernen bestätigen

> 📡 **Hinweis:** Home Assistant erkennt selbständig, welche Messdaten vom Sensor erhoben werden (Temperatur, Luftfeuchtigkeit, etc.).

---

## Entity-Typen

Die Integration erstellt automatisch passende Entities je nach Gerät:

| Entity-Typ | Plattform | Verwendung |
|------------|-----------|------------|
| **switch** | Schalter | Ein/Aus-Steuerung |
| **light** | Licht | Dimmer mit Helligkeit |
| **cover** | Abdeckung | Rollläden, Jalousien |
| **sensor** | Sensor | Messwerte, Status |
| **binary_sensor** | Binärsensor | Tastenzustand, Batterie |
| **button** | Schaltfläche | Aktionen auslösen |
| **select** | Auswahl | Modus-Umschaltung |

### Automatisch erstellte Entities

**Beispiel: EWneo-Motor**
- `cover.ewneo_motor_[serial]` – Hauptsteuerung
- `sensor.ewneo_motor_[serial]_position` – Aktuelle Position
- `sensor.ewneo_motor_[serial]_status` – Bewegungsstatus
- `button.ewneo_motor_[serial]_remove` – Gerät entfernen

**Beispiel: Easywave-Sender (4 Tasten)**
- `binary_sensor.ew_transmitter_[serial]_button_a` – Taste A
- `binary_sensor.ew_transmitter_[serial]_button_b` – Taste B
- `binary_sensor.ew_transmitter_[serial]_button_c` – Taste C
- `binary_sensor.ew_transmitter_[serial]_button_d` – Taste D
- `sensor.ew_transmitter_[serial]_last_button` – Zuletzt gedrückte Taste

---

## Services

### Geräteverwaltung

| Service | Beschreibung |
|---------|--------------|
| `easywave.add_device` | Gerät manuell hinzufügen |
| `easywave.remove_device` | Gerät entfernen |
| `easywave.remove_device_by_id` | Gerät per Device-ID entfernen |
| `easywave.list_removable_devices` | Entfernbare Geräte auflisten |
| `easywave.bulk_remove_devices` | Mehrere Geräte entfernen |

### Gerätesteuerung

| Service | Beschreibung |
|---------|--------------|
| `easywave.send_command` | Befehl an Gerät senden |
| `easywave.scan_devices` | Gerätescan starten |
| `easywave.learn_device` | Gerät einlernen |

### Wartung

| Service | Beschreibung |
|---------|--------------|
| `easywave.refresh_entity_specs` | Entity-Spezifikationen aktualisieren |
| `easywave.reset_entity_registry` | Entity-Registry zurücksetzen |
| `easywave.cleanup_orphaned_entities` | Verwaiste Entities aufräumen |
| `easywave.fix_transceiver` | Transceiver-Reparatur |

### Beispiel: Gerät manuell hinzufügen

```yaml
service: easywave.add_device
data:
  serial_number: "1234567890ABCDEF"
  device_type: "ewneo_motor"
  device_name: "Wohnzimmer Rollladen"
```

---

## Automationen

### Sender als Trigger verwenden

```yaml
automation:
  - alias: "Licht mit Fernbedienung schalten"
    trigger:
      - platform: state
        entity_id: binary_sensor.ew_transmitter_abc123_button_a
        to: "on"
    action:
      - service: light.toggle
        target:
          entity_id: light.wohnzimmer
```

### Motor-Position bei Sonnenuntergang

```yaml
automation:
  - alias: "Rollladen bei Sonnenuntergang"
    trigger:
      - platform: sun
        event: sunset
    action:
      - service: cover.set_cover_position
        target:
          entity_id: cover.ewneo_motor_abc123
        data:
          position: 20
```

### Temperaturgesteuerte Heizung

```yaml
automation:
  - alias: "Heizung bei Kälte einschalten"
    trigger:
      - platform: numeric_state
        entity_id: sensor.ewneo_sensor_abc123_temperature
        below: 18
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.ew_receiver_heizung
```

### Batteriestatus

```yaml
automation:
  - alias: "Batterie-Warnung Sender"
    trigger:
      - platform: state
        entity_id: sensor.ew_transmitter_abc123_battery_warning
        to: "low"
    action:
      - service: notify.mobile_app
        data:
          title: "Batterie schwach"
          message: "Die Batterie des Senders ist fast leer."
```

---

## Fehlerbehebung

### RX11 wird nicht erkannt

1. **USB-Verbindung prüfen:**
   ```bash
   lsusb | grep 155a
   dmesg | tail -20
   ```

2. **Berechtigungen prüfen:**
   ```bash
   ls -la /dev/ttyUSB* /dev/ttyACM*
   # Benutzer zur dialout-Gruppe hinzufügen:
   sudo usermod -aG dialout homeassistant
   ```

3. **Container-Modus:** USB-Gerät im Docker/Addon durchreichen

### Gerät reagiert nicht

1. **Reichweite prüfen** – Gerät näher an RX11 bringen
2. **Batterie prüfen** – Bei batteriebetriebenen Geräten
3. **Neu einlernen** – Gerät entfernen und erneut einlernen
4. **Logs prüfen:**
   ```yaml
   logger:
     logs:
       custom_components.easywave: debug
       custom_components.easywave.transceivers.rx11: debug
   ```

### Entities fehlen

1. **Integration neu laden:** *Einstellungen → Geräte & Dienste → Easywave → ⋮ → Neu laden*
2. **Service ausführen:** `easywave.refresh_entity_specs`
3. **Home Assistant neu starten**

### Transceiver-Fehler

Bei persistenten Kommunikationsproblemen:
```yaml
service: easywave.fix_transceiver
data: {}
```

---

## Datensicherheit

### Automatische Backups

Die Integration sichert Gerätekonfigurationen automatisch:
- **Speicherort:** `managed_devices.json`
- **Backup bei jedem Speichern**
- **Automatische Migration** bei Updates

### Update-Sicherheit

✅ Eingerichtete Geräte bleiben bei Updates erhalten  
✅ Keine manuelle Neukonfiguration erforderlich  
✅ Zentrale Geräteverwaltung (ab v2.0)

---

## Weiterführende Informationen

- **GitHub Repository:** [eldateas/HomeAssistant-Integration](https://github.com/eldateas/HomeAssistant-Integration)
- **Issue Tracker:** [Probleme melden](https://github.com/eldateas/HomeAssistant-Integration/issues)
- **ELDAT Website:** [eldat.de](https://www.eldat.de)

---

*Diese Dokumentation bezieht sich auf die Easywave Integration Version 0.6.x*

