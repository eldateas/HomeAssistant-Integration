"""Hilfsfunktionen für unique_id-Generierung, Seriennummer-Validierung und Geräte-Existenz-Prüfung."""
from typing import Optional, Tuple, List
import re
import logging

_LOGGER = logging.getLogger(__name__)


def normalize_serial_number(serial: str) -> str:
    """Stellt sicher, dass die Seriennummer als 32-Zeichen Hex-String (lowercase, ohne 0x) vorliegt.
    
    Args:
        serial: Seriennummer in beliebigem Format (Hex, mit/ohne 0x, mit Trennzeichen)
        
    Returns:
        Normalisierte Seriennummer (32 Zeichen lowercase Hex)
        
    Raises:
        ValueError: Wenn Seriennummer ungültig ist
    """
    if not serial or not isinstance(serial, str):
        raise ValueError(f"Seriennummer muss ein nicht-leerer String sein, erhalten: {serial!r}")
    
    # Bereinige Seriennummer
    serial = serial.strip().lower().replace(":", "").replace("-", "").replace(" ", "")
    
    # Entferne 0x Präfix falls vorhanden
    if serial.startswith("0x"):
        serial = serial[2:]
    
    # Validiere dass nur Hex-Zeichen vorhanden sind
    if not re.match(r'^[0-9a-f]+$', serial):
        raise ValueError(f"Seriennummer enthält nicht-Hex-Zeichen: {serial}")
    
    # Normalisiere Länge auf 32 Zeichen (16 Bytes = 32 Hex-Zeichen)
    if len(serial) < 32:
        serial = serial.zfill(32)
    elif len(serial) > 32:
        # Warnung, aber akzeptiere trotzdem (truncate zu 32)
        _LOGGER.warning("Seriennummer länger als 32 Zeichen, truncate: %s → %s", 
                       serial, serial[:32])
        serial = serial[:32]
    
    return serial


def validate_serial_number(serial: str) -> Tuple[bool, str]:
    """Validiert eine Seriennummer umfassend.
    
    Args:
        serial: Seriennummer zum Validieren
        
    Returns:
        Tuple (ist_valid, normalisierte_seriennummer_oder_fehlermeldung)
    """
    try:
        normalized = normalize_serial_number(serial)
        return (True, normalized)
    except ValueError as e:
        return (False, str(e))


def serial_number_fingerprint(serial: str) -> str:
    """Erstellt einen eindeutigen Fingerprint für eine Seriennummer.
    
    Wird verwendet, um eindeutig identifiziert zu sein, selbst wenn die Seriennummer
    in verschiedenen Formaten eingegeben wird.
    
    Args:
        serial: Seriennummer
        
    Returns:
        Eindeutiger Fingerprint (zB für Deduplication)
    """
    try:
        normalized = normalize_serial_number(serial)
        # Hash-ähnlicher Character, aber lesbar
        return normalized.upper()
    except ValueError:
        # Fallback für ungültige Nummern
        return serial.strip().upper()


def make_unique_id(serial: str, entity_type: str, channel: Optional[int] = None, suffix: Optional[str] = None) -> str:
    """Erzeugt eine einheitliche, eindeutige Entity-ID.
    
    Format: <serial>_<entity_type>_ch<channel>[_suffix]
    
    Beispiele:
        - "1234567890abcdef1234567890abcdef_switch_ch1"
        - "1234567890abcdef1234567890abcdef_sensor_temperature"
        - "1234567890abcdef1234567890abcdef_binary_sensor_door_ch2_battery"
    
    Args:
        serial: Seriennummer des Geräts
        entity_type: Typ der Entity (switch, sensor, light, etc.)
        channel: Optional: Kanalnummer (für Multi-Channel-Geräte)
        suffix: Optional: Zusätzlicher Suffix (zB 'battery' für Batterie-Sensor)
        
    Returns:
        Eindeutige Entity-ID
        
    Raises:
        ValueError: Wenn Eingaben ungültig sind
    """
    # Validiere und normalisiere Seriennummer
    try:
        serial = normalize_serial_number(serial)
    except ValueError as e:
        raise ValueError(f"Ungültige Seriennummer in make_unique_id: {e}")
    
    # Validiere entity_type
    if not entity_type or not isinstance(entity_type, str):
        raise ValueError(f"entity_type muss ein nicht-leerer String sein: {entity_type!r}")
    
    entity_type = entity_type.lower().strip()
    
    # Baue unique_id zusammen
    parts = [serial, entity_type]
    
    if channel is not None:
        try:
            channel_num = int(channel)
            if channel_num < 0 or channel_num > 255:
                raise ValueError(f"Kanalnummer muss zwischen 0-255 sein: {channel_num}")
            parts.append(f"ch{channel_num}")
        except (ValueError, TypeError) as e:
            raise ValueError(f"Ungültige Kanalnummer: {e}")
    
    if suffix:
        suffix = str(suffix).lower().strip()
        if not re.match(r'^[a-z0-9_]+$', suffix):
            raise ValueError(f"Suffix darf nur alphanumerisch und _ enthalten: {suffix!r}")
        parts.append(suffix)
    
    unique_id = "_".join(parts)
    
    # Validiere Länge (HA limit ist ~255)
    if len(unique_id) > 255:
        raise ValueError(f"Unique ID zu lang (max 255): {len(unique_id)} Zeichen")
    
    return unique_id
