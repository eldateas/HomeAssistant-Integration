# EWneo-Sensor einlernen

Diese Anleitung beschreibt die Schritte zum Einlernen eines EWneo-Sensors (Temperatur/Feuchte/Wetter) in Home Assistant.

## Schritt 1: Gerätetyp auswählen

Im Dialog **"Gerätetyp auswählen"** wählen Sie:

- **🌡️ EWneo-Sensor (Temperatur/Feuchte)**

Weitere Optionen im Menü:
- 🎛️ EW-Sender (Fernbedienung)
- 📥 EW-Receiver (Schalter/Motor/Heizung)
- 📥 EWneo-Schalter/Motor (Bidirektional)
- ❌ Abbrechen

---

## Schritt 2: EWneo-Sensor Lernen

Im Dialog **"EWneo-Sensor Lernen"** wird angezeigt:

```
🌡️ EWneo-Sensor Lernen

📋 Gerätetyp: EWneo-Sensor (Temperatur/Feuchte/Wetter)

📊 Auto-Erkennung:
• 🌡️ Temperatursensor
• 💧 Feuchtesensor
• ☔ Regensensor
• 💨 Windsensor
• 🔋 Batteriestatus

🔴 Wichtig: Drücken Sie die Lerntaste am EWneo-Sensor, 
wenn das Lernen gestartet wird!
```

Optionen:
- **▶️ Lernen starten** - Startet den Lernvorgang
- **⬅️ Zurück** - Zurück zur Geräteauswahl

---

## Schritt 3: Lernvorgang

Nach Klick auf **"Lernen starten"** erscheint:

```
🔄 Warte auf Lernsignal... 
Drücken Sie JETZT die Lerntaste am EWneo-Sensor!
```

**Aktion:** Drücken Sie jetzt die **Lerntaste** an Ihrem EWneo-Sensor.

> **Hinweis:** Sie müssen die Lerntaste drücken, nicht nur warten auf ein normales Messsignal.

---

## Mögliche Ergebnisse

### Erfolg

Bei erfolgreichem Empfang erscheint der Dialog **"EWneo-Sensor bestätigen"**:

```
✅ EWneo-Sensor erfolgreich erkannt!

🔍 Erkannte Gerätedaten:
• Seriennummer: AB12CD34
• Name: EWneo-Sensor #1
• Erkannte Sensoren:
  - Temperatur
  - Luftfeuchtigkeit
  - Batterie

⚡ Was wird angelegt:
• 1 EWneo-Sensor Gerät
• Sensor-Entitäten für alle erkannten Messwerte
• Batteriestatus-Sensor

⚙️ Gerät anlegen:
Wählen Sie 'Anlegen' um das Gerät zu erstellen.
```

Eingabefelder:
- **Gerätename** - Name des Sensors anpassen

### Timeout

Wenn kein Lernsignal empfangen wurde:

```
⏰ Kein Lernsignal empfangen

Das System hat kein Lernsignal empfangen. 
Stellen Sie sicher, dass Sie die Lerntaste am Sensor drücken 
(nicht nur ein normales Messsignal).
```

Optionen:
- **🔄 Erneut versuchen** - Lernvorgang wiederholen
- **⬅️ Zurück** - Zurück zur Sensor-Beschreibung

---

## Mögliche Fehlermeldungen

### Messung ohne Lernen

```
Measurement telegram received, but sensor must be learned first. 
Press the learn button on the sensor and try again.
```

Sie haben ein Messsignal empfangen, aber kein Lernsignal. Drücken Sie die Lerntaste.

### Ungültiges Sensor-Telegramm

```
Invalid sensor telegram received. 
Please press the learn button on the sensor.
```

Das empfangene Telegramm ist ungültig. Drücken Sie die Lerntaste am Sensor.

### Warten auf gültiges Telegramm

```
Waiting for valid sensor telegram. 
Please press the learn button on the EWneo-Sensor 
(not on the EWneo-Switch/Motor).
```

Stellen Sie sicher, dass Sie einen EWneo-Sensor einlernen (nicht einen EWneo-Schalter/Motor).

---

## Zusammenfassung der Schritte

```
Gerätetyp auswählen
    └── 🌡️ EWneo-Sensor (Temperatur/Feuchte)
            └── EWneo-Sensor Lernen
                    └── ▶️ Lernen starten
                            └── Lerntaste am Sensor drücken
                                    ├── ✅ Erfolg → Sensor bestätigen
                                    │                   └── Gerätename eingeben
                                    │                           └── Anlegen
                                    └── ⏰ Timeout → Erneut versuchen
```

---

📖 **Weitere Dokumentation:**
- [EW-Sender einlernen](learning_ew_transmitter.md)
- [EW-Empfänger einlernen](learning_ew_receiver.md)
- [Dokumentations-Übersicht](index.md)
