"""EWneo sensor implementations for RX11 transceiver.

The EWneoSensor class provides a flexible, universal sensor implementation
that can handle any combination of sensor types (temperature, humidity, wind, rain).
Sensor capabilities are configured during initialization based on learning data.

All RX11-specific telegram processing and data extraction is handled in the
EWneoSensor class based on neo_type values.
"""

from .ewneo_sensor import (
    EWneoSensor,
    create_ewneo_sensor,
    create_temperature_sensor,
    create_humidity_sensor,
    create_wind_sensor,
    create_rain_sensor,
    SENSOR_TYPE_CONFIGS,
    NEO_TYPE_SENSOR_MAP,
)

__all__ = [
    "EWneoSensor",
    "create_ewneo_sensor",
    "create_temperature_sensor",
    "create_humidity_sensor",
    "create_wind_sensor",
    "create_rain_sensor",
    "SENSOR_TYPE_CONFIGS",
    "NEO_TYPE_SENSOR_MAP",
]
