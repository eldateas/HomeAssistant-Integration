# EW-Sender einlernen

Diese Anleitung beschreibt die Schritte zum Einlernen eines EW-Senders (Fernbedienung) in Home Assistant.

## Schritt 1: Gerätetyp auswählen

Im Dialog **"Gerätetyp auswählen"** wählen Sie:

- **🎛️ EW-Sender (Fernbedienung)**

Weitere Optionen im Menü:
- 🌡️ EWneo-Sensor (Temperatur/Feuchte)
- 📥 EW-Receiver (Schalter/Motor/Heizung)
- 📥 EWneo-Schalter/Motor (Bidirektional)
- ❌ Abbrechen

---

## Schritt 2: EW-Sender Konfiguration

Im Dialog **"EW-Sender Konfiguration"** wählen Sie die Tastenbedienung:

| Option | Beschreibung |
|--------|--------------|
| **🔘 1-Tast-Bedienung** | Toggle-Modus |
| **🔘🔘 2-Tast-Bedienung** | Paarweise Tasten |
| **🔘🔘🔘 3-Tast-Bedienung (Auf/Zu/Stopp)** | Für Motoren |
| **⬅️ Zurück** | Zurück zur Geräteauswahl |

---

## Pfad A: 1-Tast-Bedienung

### Schritt 2a: Gruppierung

Im Dialog **"Gruppierung"** wählen Sie:

| Option | Beschreibung |
|--------|--------------|
| **🔘 Einzeln schalten** | Jede Taste einzeln |
| **🔗 Als Gruppe schalten** | Tasten als Gruppe |
| **⬅️ Zurück** | Zurück zur Konfiguration |

### Schritt 2b: Schaltmodus

Im Dialog **"Schaltmodus"** wählen Sie:

| Option | Beschreibung |
|--------|--------------|
| **⚡ Impuls (Zustand wird zurückgesetzt)** | Zustand wird nach Tastendruck zurückgesetzt |
| **🔒 Dauer (Zustand bleibt erhalten)** | Zustand bleibt bestehen |
| **⬅️ Zurück** | Zurück zur Gruppierung |

### Schritt 2c: Tastenanzahl

Im Dialog **"Tastenanzahl"** wählen Sie:

| Option |
|--------|
| **🔘 1 Taste** |
| **🔘🔘 2 Tasten** |
| **🔘🔘🔘 3 Tasten** |
| **🔘🔘🔘🔘 4 Tasten** |
| **⬅️ Zurück** |

---

## Pfad B: 2-Tast-Bedienung

### Schritt 2a: Verwendung

Im Dialog **"2-Tast-Verwendung"** wählen Sie:

| Option | Beschreibung |
|--------|--------------|
| **🔌 EIN/AUS (Schalter)** | Für Schalter |
| **🏠 AUF/ZU (Rollladen/Jalousie)** | Für Rollläden |
| **⬅️ Zurück** | Zurück zur Konfiguration |

### Schritt 2b: Tastenanzahl

Im Dialog **"Tastenanzahl (2-Tast)"** wählen Sie:

| Option |
|--------|
| **🔘🔘 2 Tasten** |
| **🔘🔘🔘🔘 4 Tasten** |
| **⬅️ Zurück** |

---

## Schritt 3: EW-Sender Lernen

Im Dialog **"EW-Sender Lernen"** werden die konfigurierten Gerätedaten angezeigt:

```
🎛️ EW-Sender Lernen

📋 Gerätedaten:
• Name: EW-Sender #1
• Tasten: 2
• Modus: 1-Tast-Bedienung

📡 Lernprozess:
Drücken Sie eine beliebige Taste auf dem EW-Sender, 
wenn das Lernen gestartet wird.

🔴 Wichtig: Halten Sie den EW-Sender bereit!
```

Optionen:
- **▶️ Lernen starten** - Startet den Lernvorgang
- **⬅️ Zurück** - Zurück zur Konfiguration

---

## Schritt 4: Lernvorgang

Nach Klick auf **"Lernen starten"** erscheint:

```
🔄 Laden & Warten auf Funksignal...

🔴 Drücken Sie JETZT eine Taste am EW-Sender!

⏳ Das System wartet bis zu 30 Sekunden auf das Telegramm.
```

**Aktion:** Drücken Sie jetzt eine beliebige Taste auf Ihrem EW-Sender.

---

## Mögliche Ergebnisse

### Erfolg

Bei erfolgreichem Empfang wird das Gerät erkannt und kann bestätigt werden.

### Timeout

Wenn kein Signal empfangen wurde:

```
⏰ Kein Signal empfangen

Das System hat kein Funksignal empfangen. 
Sie können es erneut versuchen.
```

Optionen:
- **🔄 Erneut versuchen** - Lernvorgang wiederholen
- **⬅️ Zurück zur Konfiguration** - Einstellungen ändern

### Gerät bereits vorhanden

Wenn der Sender bereits eingelernt ist:

```
Dieser Easywave Sender ist bereits eingelernt!

Der Sendecode wird schon als "EW-Sender #1" 
in Home Assistant verwendet.

Bitte wiederholen Sie den Vorgang und verwenden Sie 
einen noch nicht eingelernten Sender.
```

Optionen:
- **🔄 Sender einlernen** - Mit anderem Sender versuchen
- **⬅️ Zurück** - Zurück zur Konfiguration

---

## Zusammenfassung der Pfade

```
Gerätetyp auswählen
    └── 🎛️ EW-Sender (Fernbedienung)
            └── EW-Sender Konfiguration
                    ├── 🔘 1-Tast-Bedienung
                    │       └── Gruppierung
                    │               └── Schaltmodus
                    │                       └── Tastenanzahl
                    │                               └── Lernen
                    ├── 🔘🔘 2-Tast-Bedienung
                    │       └── Verwendung
                    │               └── Tastenanzahl
                    │                       └── Lernen
                    └── 🔘🔘🔘 3-Tast-Bedienung
                            └── Lernen
```

---

📖 **Weitere Dokumentation:**
- [EW-Empfänger einlernen](learning_ew_receiver.md)
- [EWneo-Sensoren einlernen](learning_ewneo_sensor.md)
- [Dokumentations-Übersicht](index.md)
