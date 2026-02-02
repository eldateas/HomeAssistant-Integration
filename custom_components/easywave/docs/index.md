# ELDAT EasyWave Dokumentation

Willkommen zur Dokumentation der ELDAT EasyWave Integration für Home Assistant.

## Geräte einlernen

| Dokumentation | Beschreibung |
|---------------|--------------|
| [EW-Sender einlernen](learning_ew_transmitter.md) | Fernbedienungen und Wandtaster |
| [EW-Empfänger einlernen](learning_ew_receiver.md) | Schalter, Motoren und Heizungen |
| [EWneo-Sensoren einlernen](learning_ewneo_sensor.md) | Temperatur-, Feuchte- und Wettersensoren |

## Übersicht der Gerätetypen

### EW-Sender (Fernbedienung)

EW-Sender sind batteriebetriebene Fernbedienungen, die Funksignale senden.

**Auswahl im Dialog:** 🎛️ EW-Sender (Fernbedienung)

**Tastenbedienungen:**
- 1-Tast-Bedienung (Toggle)
- 2-Tast-Bedienung (An/Aus oder Auf/Zu)
- 3-Tast-Bedienung (Auf/Zu/Stopp)

### EW-Empfänger (Receiver)

EW-Empfänger sind Aktoren, die Funksignale empfangen und Geräte schalten.

**Auswahl im Dialog:** 📥 EW-Receiver (Schalter/Motor/Heizung)

**Gerätetypen:**
- Switch (Schalter)
- Motor/Cover (Rollläden/Jalousien)
- Heating/Cooling (Heizung/Kühlung)

### EWneo-Sensor

EWneo-Sensoren sind Umgebungssensoren mit automatischer Erkennung.

**Auswahl im Dialog:** 🌡️ EWneo-Sensor (Temperatur/Feuchte)

**Auto-erkannte Sensortypen:**
- Temperatursensor
- Feuchtesensor
- Regensensor
- Windsensor
- Batteriestatus

## Allgemeiner Ablauf

1. **Einstellungen** → **Geräte & Dienste** öffnen
2. **ELDAT EasyWave** Integration suchen
3. **Gerät hinzufügen** (+) klicken
4. Gerätetyp auswählen
5. Konfiguration durchführen
6. Lernvorgang starten
7. Taste/Lerntaste am Gerät drücken
8. Gerät bestätigen und anlegen

## Fehlerbehebung

### Allgemeine Probleme

| Problem | Lösung |
|---------|--------|
| Kein Signal empfangen | Gerät näher an RX11 bringen |
| Timeout nach 30 Sekunden | Batterien prüfen, erneut versuchen |
| Gerät bereits vorhanden | Anderes Gerät verwenden oder bestehendes entfernen |

### RX11 Transceiver

Der RX11 USB Transceiver muss angeschlossen und eingerichtet sein, bevor Geräte eingelernt werden können.

---

📖 **Hauptseite:** [README.md](../README.md)
