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
    
    # Validate that only hex characters are present
    if not re.match(r'^[0-9a-f]+$', serial):
        raise ValueError(f"Serial number contains non-hex characters: {serial}")
    
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


def make_unique_id(registration_id: str, entity_type: str, channel: Optional[int] = None, suffix: Optional[str] = None) -> str:
    """Erzeugt eine einheitliche, eindeutige Entity-ID.
    
    Format: <registration_id>_<entity_type>[_ch<channel>]
    
    Die registration_id (UUID) ist der alleinige Geräte-Identifikator.
    Ein optionaler suffix wird NICHT mehr benötigt (die registration_id
    selbst ist bereits pro Lernvorgang einzigartig).
    
    Beispiele:
        - "b0dcc3b8e54f0b2c413f208747d9f5c8_switch"
        - "b0dcc3b8e54f0b2c413f208747d9f5c8_cover_ch1"
    
    Args:
        registration_id: UUID-basierte Registrierungs-ID des Geräts
        entity_type: Typ der Entity (switch, sensor, light, etc.)
        channel: Optional: Kanalnummer (für Multi-Channel-Geräte)
        suffix: IGNORIERT – nur noch aus Kompatibilitätsgründen akzeptiert
        
    Returns:
        Eindeutige Entity-ID
        
    Raises:
        ValueError: Wenn Eingaben ungültig sind
    """
    if not registration_id or not isinstance(registration_id, str):
        raise ValueError(f"registration_id muss ein nicht-leerer String sein: {registration_id!r}")
    
    # registration_id ist bereits eine UUID (lowercase hex) – normalisiere trotzdem
    registration_id = registration_id.strip().lower()
    
    # Validate entity_type
    if not entity_type or not isinstance(entity_type, str):
        raise ValueError(f"entity_type must be a non-empty string: {entity_type!r}")
    
    entity_type = entity_type.lower().strip()
    
    # Baue unique_id zusammen
    parts = [registration_id, entity_type]
    
    if channel is not None:
        try:
            channel_num = int(channel)
            if channel_num < 0 or channel_num > 255:
                raise ValueError(f"Channel number must be between 0-255: {channel_num}")
            parts.append(f"ch{channel_num}")
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid channel number: {e}")
    
    # suffix wird ignoriert – registration_id ist bereits einzigartig pro Lernvorgang
    
    unique_id = "_".join(parts)
    
    # Validate length (HA limit is ~255)
    if len(unique_id) > 255:
        raise ValueError(f"Unique ID too long (max 255): {len(unique_id)} chars")
    
    return unique_id
