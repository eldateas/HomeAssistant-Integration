"""Entity specs mixin for automatic entity specification generation."""
from __future__ import annotations

from typing import Any, Dict, List


class EntitySpecsMixin:
    """Mixin für automatische Entity-Spezifikations-Generierung."""
    
    def get_entity_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """Generate entity specifications for this device."""
        specs = {
            "switch": [],
            "light": [],
            "cover": [],
            "sensor": [],
            "binary_sensor": [],
            "button": []
        }
        
        # Wird von konkreten Device-Klassen überschrieben
        return specs
    
    def _create_base_entity_spec(self, entity_type: str, channel: int = 0, **kwargs) -> Dict[str, Any]:
        """Create a base entity specification."""
        channel_suffix = f"_ch{channel}" if channel > 0 else ""
        
        spec = {
            "type": entity_type,
            "name": kwargs.get("name", f"{self.name} {entity_type.title()}{channel_suffix}"),
            "unique_id": f"{self.serial_number}_{entity_type}{channel_suffix}",
            "channel": channel,
            "device_class": kwargs.get("device_class"),
            "icon": kwargs.get("icon"),
            "unit_of_measurement": kwargs.get("unit_of_measurement"),
        }
        
        # Remove None values
        spec = {k: v for k, v in spec.items() if v is not None}
        
        return spec
