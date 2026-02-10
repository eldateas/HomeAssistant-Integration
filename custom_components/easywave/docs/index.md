# ELDAT EasyWave Dokumentation

Willkommen zur Dokumentation der ELDAT EasyWave Integration für Home Assistant.

## Geräte einlernen

| Dokumentation | Beschreibung |
|---------------|--------------|
| [Easywave Sender einlernen](step_device_transmitter_config.md) | Fernbedienungen und Wandtaster |
| [Easywave Empfänger einlernen](step_device_receiver_type.md) | Schalter, Motoren und Heizungen |
| [EWneo-Sensoren einlernen](step_device_sensor_description.md) | Temperatur-, Feuchte- und Wettersensoren |
| [EWneo-Empfänger einlernen](step_device_ewneo_receiver.md) | Bidirektionale Empfänger |

## Dialog-Steps Referenz

### Initial Setup

| Step | Dokumentation |
|------|---------------|
| RX11 USB Transceiver einrichten | [step_rx11_setup.md](step_rx11_setup.md) |
| Neues ELDAT Gerät hinzufügen | [step_device.md](step_device.md) |

### Gerätetyp Auswahl

| Step | Dokumentation |
|------|---------------|
| Geräteauswahl | [step_device_type_select.md](step_device_type_select.md) |

### Easywave Sender Lernprozess

| Step | Dokumentation |
|------|---------------|
| Easywave Sender einlernen | [step_device_transmitter.md](step_device_transmitter.md) |
| Betriebsart | [step_device_transmitter_config.md](step_device_transmitter_config.md) |
| Bedienung (Gruppierung) | [step_device_transmitter_grouping.md](step_device_transmitter_grouping.md) |
| Verhalten (Impuls/Dauer) | [step_device_transmitter_switch_mode.md](step_device_transmitter_switch_mode.md) |
| Tastenanzahl | [step_device_transmitter_button_count.md](step_device_transmitter_button_count.md) |
| Zustände (2-Tast) | [step_device_transmitter_2button_usage.md](step_device_transmitter_2button_usage.md) |
| Tastenanzahl (2-Tast) | [step_device_transmitter_2button_button_count.md](step_device_transmitter_2button_button_count.md) |
| Sender einlernen - Zusammenfassung | [step_device_transmitter_description.md](step_device_transmitter_description.md) |
| Sendertaste betätigen | [step_device_transmitter_learn_progress.md](step_device_transmitter_learn_progress.md) |
| Zeitüberschreitung (Sender) | [step_device_transmitter_learn_timeout.md](step_device_transmitter_learn_timeout.md) |
| Sender bereits vorhanden | [step_device_transmitter_already_exists.md](step_device_transmitter_already_exists.md) |
| Einlernen erfolgreich (Sender) | [step_device_transmitter_verify.md](step_device_transmitter_verify.md) |

### EWneo-Sensor Lernprozess

| Step | Dokumentation |
|------|---------------|
| EWneo-Sensoren einlernen | [step_device_sensor.md](step_device_sensor.md) |
| Sensor einlernen - Einleitung | [step_device_sensor_description.md](step_device_sensor_description.md) |
| Lerntaste betätigen | [step_device_sensor_learn_progress.md](step_device_sensor_learn_progress.md) |
| Zeitüberschreitung (Sensor) | [step_device_sensor_learn_timeout.md](step_device_sensor_learn_timeout.md) |
| Einlernen erfolgreich (Sensor) | [step_device_sensor_verify.md](step_device_sensor_verify.md) |
| Sensor bereits vorhanden | [step_device_sensor_already_exists.md](step_device_sensor_already_exists.md) |

### EWneo-Empfänger Lernprozess (Bidirektional)

| Step | Dokumentation |
|------|---------------|
| EWneo Empfänger hinzufügen | [step_device_ewneo_receiver.md](step_device_ewneo_receiver.md) |
| EWneo Empfänger vorbereiten | [step_device_ewneo_receiver_prepare.md](step_device_ewneo_receiver_prepare.md) |
| EWneo Empfänger einlernen | [step_device_ewneo_receiver_learn.md](step_device_ewneo_receiver_learn.md) |
| EWneo Empfänger - Warten | [step_device_ewneo_receiver_learn_wait.md](step_device_ewneo_receiver_learn_wait.md) |
| Timeout (EWneo Empfänger) | [step_device_ewneo_receiver_learn_timeout.md](step_device_ewneo_receiver_learn_timeout.md) |
| EWneo Empfänger bereits vorhanden | [step_device_ewneo_receiver_already_exists.md](step_device_ewneo_receiver_already_exists.md) |
| EWneo Empfänger einlernen - Beschreibung | [step_device_ewneo_receiver_description.md](step_device_ewneo_receiver_description.md) |
| Empfänger in Programmiermodus | [step_device_ewneo_receiver_programming_mode.md](step_device_ewneo_receiver_programming_mode.md) |
| EWneo-Transceiver Lernmodus | [step_device_ewneo_receiver_learning_mode.md](step_device_ewneo_receiver_learning_mode.md) |
| EWneo Empfänger verbinden | [step_device_ewneo_receiver_join.md](step_device_ewneo_receiver_join.md) |
| Einlernen erfolgreich (EWneo) | [step_device_ewneo_receiver_verify.md](step_device_ewneo_receiver_verify.md) |
| EWneo Empfänger bestätigen | [step_device_ewneo_receiver_confirm.md](step_device_ewneo_receiver_confirm.md) |

### Easywave Empfänger Konfiguration (Unidirektional)

| Step | Dokumentation |
|------|---------------|
| Betriebsart | [step_device_receiver_type.md](step_device_receiver_type.md) |
| Empfänger einlernen - Beschreibung | [step_device_receiver_description.md](step_device_receiver_description.md) |
| Einlernen bestätigen | [step_device_receiver_confirm_learning.md](step_device_receiver_confirm_learning.md) |
| Einlernen erfolgreich (Easywave Empfänger) | [step_device_receiver_verify.md](step_device_receiver_verify.md) |
| Easywave Receiver Bestätigung | [step_device_receiver_confirm.md](step_device_receiver_confirm.md) |
| Easywave Receiver einlernen | [step_device_receiver.md](step_device_receiver.md) |
| Easywave Receiver konfigurieren | [step_ew_receiver_config.md](step_ew_receiver_config.md) |
| Easywave Receiver Lernmodus | [step_ew_receiver_learn.md](step_ew_receiver_learn.md) |
| Easywave Receiver erstellen | [step_ew_receiver_final_confirm.md](step_ew_receiver_final_confirm.md) |

### Gerätebestätigung & Abschluss

| Step | Dokumentation |
|------|---------------|
| Gerät bestätigen | [step_device_confirm.md](step_device_confirm.md) |
| Anlegen erfolgreich | [step_device_save_success.md](step_device_save_success.md) |

### Manuelle & Erweiterte Optionen

| Step | Dokumentation |
|------|---------------|
| Alle USB-Geräte anzeigen | [step_show_all_devices.md](step_show_all_devices.md) |
| ELDAT USB Gerät manuell eingeben | [step_manual.md](step_manual.md) |
| ELDAT USB Gerät bestätigen | [step_usb_confirm.md](step_usb_confirm.md) |
| ELDAT Gerät hinzufügen | [step_device_add_select.md](step_device_add_select.md) |
| Lernmodus aktiv | [step_device_add_learn.md](step_device_add_learn.md) |
| Gerät bestätigen | [step_device_add_confirm.md](step_device_add_confirm.md) |

### Optionen

| Step | Dokumentation |
|------|---------------|
| ELDAT Optionen | [step_options_init.md](step_options_init.md) |

