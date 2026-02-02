# EW-Empfänger einlernen

Diese Anleitung beschreibt die Schritte zum Einlernen eines EW-Empfängers (Schalter/Motor/Heizung) in Home Assistant.

## Schritt 1: Gerätetyp auswählen

Im Dialog **"Gerätetyp auswählen"** wählen Sie:

- **📥 EW-Receiver (Schalter/Motor/Heizung)**

Weitere Optionen im Menü:
- 🎛️ EW-Sender (Fernbedienung)
- 🌡️ EWneo-Sensor (Temperatur/Feuchte)
- 📥 EWneo-Schalter/Motor (Bidirektional)
- ❌ Abbrechen

---

## Schritt 2: Automatisches Laden

Im Dialog **"EW-Receiver - Automatisches Laden"** wird angezeigt:

```
Seriennummer wird automatisch vom RX11 geladen...
```

Das System lädt automatisch die verfügbaren EW-Receiver Seriennummern vom RX11 Transceiver.

---

## Schritt 3: Gerätetyp wählen

Im Dialog **"EW-Receiver - Gerätetyp"** werden die Gerätedaten angezeigt:

```
📥 EW-Receiver Konfiguration

📋 Gerätedaten:
• Gerät: EW-Receiver #1
• Seriennummer: 1234567890AB

🎛️ Wählen Sie den Gerätetyp:
```

| Option | Beschreibung |
|--------|--------------|
| **🔌 Switch** | Schalter-Empfänger |
| **🏠 Motor/Cover** | Motor/Rollladen-Empfänger |
| **🌡️ Heating/Cooling** | Heizung/Kühlung-Empfänger |
| **⬅️ Zurück** | Zurück zur Geräteauswahl |

---

## Schritt 4: Betriebsmodus wählen

Im Dialog **"EW-Receiver - Betriebsmodus"** wählen Sie:

```
📥 EW-Receiver Konfiguration

🎛️ Wählen Sie den Betriebsmodus:
```

| Option | Beschreibung |
|--------|--------------|
| **🔘 1-Tast (Toggle)** | Toggle-Modus mit einer Taste |
| **🔘🔘 2-Tast (An/Aus oder Auf/Zu)** | Zwei Tasten für An/Aus oder Auf/Zu |
| **🔘🔘🔘 3-Tast (Auf + Zu + Stopp)** | Drei Tasten für Auf, Zu und Stopp |
| **⬅️ Zurück** | Zurück zur Gerätetyp-Auswahl |

---

## Schritt 5: Bestätigung und Anlegen

Im Dialog **"EW-Receiver - Bestätigung"** wird die Konfiguration zusammengefasst:

```
📥 EW-Receiver Konfiguration

📋 Konfiguration:
• Gerät: EW-Receiver #1
• Seriennummer: 1234567890AB
• Typ: Switch
• Modus: 2-Tast

📡 Lernprozess:
Versetzen Sie den Receiver in den Lernmodus, 
dann legen Sie das Gerät an.
```

Optionen:
- **✅ Anlegen & Code A senden** - Gerät erstellen und Lerncode senden
- **⬅️ Zurück zum Modus** - Zurück zur Betriebsmodus-Auswahl

---

## Lernvorgang am Empfänger

**Vor dem Klick auf "Anlegen & Code A senden":**

1. Gehen Sie zum physischen EW-Receiver
2. Drücken Sie die Lerntaste am Empfänger
3. Die LED am Empfänger sollte blinken (Lernmodus aktiv)
4. Kehren Sie zum Dialog zurück und klicken Sie auf **"Anlegen & Code A senden"**

---

## Manuelle Konfiguration

Im Dialog **"EW-Receiver - Manual Configuration"** können Sie die Seriennummer manuell eingeben:

| Feld | Beschreibung |
|------|--------------|
| **Serial Number** | Seriennummer des Empfängers |
| **Device Name** | Gerätename |
| **RX11-Index (optional)** | RX11-Index (falls bekannt) |

---

## Mögliche Fehlermeldungen

### Keine Empfänger gefunden

```
No EW-Receivers found. Check the connection to the RX11.
```

Prüfen Sie die Verbindung zum RX11 Transceiver.

### Kommunikationsfehler

```
Communication test with EW-Receiver failed. 
Check if the receiver is in learning mode.
```

Stellen Sie sicher, dass der Empfänger im Lernmodus ist.

### Keine verfügbaren Empfänger

```
No available EW-Receivers found (all indices occupied).
```

Alle RX11-Indizes sind belegt. Entfernen Sie zuerst ein bestehendes Gerät.

---

## Zusammenfassung der Schritte

```
Gerätetyp auswählen
    └── 📥 EW-Receiver (Schalter/Motor/Heizung)
            └── Automatisches Laden (Seriennummer vom RX11)
                    └── Gerätetyp wählen
                            ├── 🔌 Switch
                            ├── 🏠 Motor/Cover
                            └── 🌡️ Heating/Cooling
                                    └── Betriebsmodus wählen
                                            ├── 🔘 1-Tast (Toggle)
                                            ├── 🔘🔘 2-Tast
                                            └── 🔘🔘🔘 3-Tast
                                                    └── Bestätigung
                                                            └── ✅ Anlegen & Code A senden
```

---

📖 **Weitere Dokumentation:**
- [EW-Sender einlernen](learning_ew_transmitter.md)
- [EWneo-Sensoren einlernen](learning_ewneo_sensor.md)
- [Dokumentations-Übersicht](index.md)
