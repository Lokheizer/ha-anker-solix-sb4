"""Calculated sensors (fork feature).

A device YAML may declare a ``derived_quantities`` section. Its entries are not
read via Modbus but calculated from other data points of the same poll cycle:

    derived_quantities:
      pv1_power:
        translation_key: "pv1_power"
        unit: "W"
        device_class: "power"
        state_class: "measurement"
        operation: "product"        # all sources multiplied
        sources: ["pv1_voltage", "pv1_current"]
        min_value: 0                # optional lower clamp
        precision: 0                # optional number of decimals
      pv4_power:
        operation: "difference"     # first source minus all following sources
        sources: ["pv_power", "pv1_power", "pv2_power", "pv3_power"]
      heating_power:
        operation: "balance"        # sum of sources minus sum of subtract
        sources: ["pv_power", "battery_power"]
        subtract: ["active_power"]  # optional, same kind of keys as sources

Sources are data point keys (``read_quantities`` etc.) or derived quantities
defined earlier in the section. The section is ignored by
``parse_device_configuration``, so it never reaches the Modbus read path.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.typing import StateType

from .base_entity import AnkerSolixBaseEntity

if TYPE_CHECKING:
    from .coordinator import AnkerSolixOfficialCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SECTION = "derived_quantities"
OPERATIONS = ("product", "difference", "balance")


def _is_number(value: Any) -> bool:
    """Return True for a finite int/float (bool is not a measurement)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_key_list(value: Any) -> bool:
    """Return True for a non-empty list of data point keys."""
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) for item in value)
    )


def _definition_error(definition: Any) -> str | None:
    """Return why a derived quantity definition is unusable, or None if valid."""
    if not isinstance(definition, dict):
        return "definition is not a mapping"
    if definition.get("operation") not in OPERATIONS:
        return f"operation must be one of {OPERATIONS}"
    if not _is_key_list(definition.get("sources")):
        return "sources must be a non-empty list of data point keys"
    if "subtract" in definition:
        if definition["operation"] != "balance":
            return "subtract is only supported by operation 'balance'"
        if not _is_key_list(definition["subtract"]):
            return "subtract must be a non-empty list of data point keys"
    return None


def get_derived_definitions(full_config: Any) -> dict[str, dict[str, Any]]:
    """Return the valid ``derived_quantities`` entries of a device config."""
    if not isinstance(full_config, dict):
        return {}
    section = full_config.get(CONFIG_SECTION)
    if not isinstance(section, dict):
        return {}

    definitions: dict[str, dict[str, Any]] = {}
    for key, definition in section.items():
        error = _definition_error(definition)
        if error:
            _LOGGER.warning("Ignoring derived quantity '%s': %s", key, error)
            continue
        definitions[key] = definition
    return definitions


def _compute_one(
    definition: dict[str, Any],
    data: dict[str, Any],
    results: dict[str, float | int | None],
) -> float | int | None:
    """Calculate one derived quantity, or None if a source has no value."""

    def _collect(keys: list[str]) -> list[float] | None:
        collected: list[float] = []
        for source in keys:
            value = results[source] if source in results else data.get(source)
            # None instead of a substituted 0: a source that was not read this
            # cycle must surface as "unknown", never as a fabricated measurement.
            if not _is_number(value):
                return None
            collected.append(float(value))
        return collected

    values = _collect(definition["sources"])
    if values is None:
        return None

    if definition["operation"] == "product":
        result = math.prod(values)
    elif definition["operation"] == "balance":
        subtracted = _collect(definition.get("subtract", []))
        if subtracted is None:
            return None
        result = sum(values) - sum(subtracted)
    else:
        result = values[0] - sum(values[1:])

    min_value = definition.get("min_value")
    if _is_number(min_value):
        result = max(result, float(min_value))

    precision = definition.get("precision")
    if isinstance(precision, int) and not isinstance(precision, bool):
        if precision <= 0:
            return round(result)
        return round(result, precision)
    return result


def compute_derived_values(
    definitions: dict[str, dict[str, Any]], data: dict[str, Any] | None
) -> dict[str, float | int | None]:
    """Calculate all derived quantities from one frame of coordinator data.

    Definitions are evaluated in order, so later ones can use earlier results
    (already clamped and rounded) as a source.
    """
    frame = data if isinstance(data, dict) else {}
    results: dict[str, float | int | None] = {}
    for key, definition in definitions.items():
        results[key] = _compute_one(definition, frame, results)
    return results


def _parse_declared_class(enum_cls: type, raw: Any, entity_key: str) -> Any | None:
    if raw is None or str(raw).lower() == "none":
        return None
    try:
        return enum_cls(str(raw).lower())
    except ValueError:
        _LOGGER.warning(
            "Ignoring invalid %s '%s' declared for %s",
            enum_cls.__name__,
            raw,
            entity_key,
        )
        return None


class AnkerSolixDerivedSensor(AnkerSolixBaseEntity, SensorEntity):
    """Sensor calculated from other data points of the same device."""

    def __init__(
        self,
        coordinator: AnkerSolixOfficialCoordinator,
        key: str,
        config: dict[str, Any],
        definitions: dict[str, dict[str, Any]],
    ) -> None:
        """Initialize the sensor.

        Args:
            coordinator: Data coordinator instance
            key: Key of this derived quantity
            config: Definition of this derived quantity
            definitions: All derived quantities of the device (for chaining)
        """
        super().__init__(coordinator, key, config)
        self._definitions = definitions

        unit = config.get("unit")
        if unit and unit != "/":
            self._attr_native_unit_of_measurement = unit
        self._attr_device_class = _parse_declared_class(
            SensorDeviceClass, config.get("device_class"), key
        )
        self._attr_state_class = _parse_declared_class(
            SensorStateClass, config.get("state_class"), key
        )
        precision = config.get("precision")
        if (
            isinstance(precision, int)
            and not isinstance(precision, bool)
            and precision >= 0
        ):
            self._attr_suggested_display_precision = precision

    @property
    def native_value(self) -> StateType:
        """Return the calculated value."""
        if not self.available:
            return None
        return compute_derived_values(self._definitions, self.coordinator.data).get(
            self._entity_key
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return how the value is calculated (static, never changes)."""
        attrs: dict[str, Any] = {
            "operation": self._config.get("operation"),
            "sources": list(self._config.get("sources", [])),
        }
        if "subtract" in self._config:
            attrs["subtract"] = list(self._config["subtract"])
        return attrs


def create_derived_sensors(
    coordinator: AnkerSolixOfficialCoordinator,
) -> list[AnkerSolixDerivedSensor]:
    """Create the calculated sensors declared in the device configuration."""
    definitions = get_derived_definitions(
        getattr(coordinator, "_full_config_cache", None)
    )
    if not definitions:
        return []

    data_points = getattr(coordinator, "_device_config_cache", None)
    if not isinstance(data_points, dict):
        data_points = {}

    sensors: list[AnkerSolixDerivedSensor] = []
    for key, definition in definitions.items():
        if key in data_points:
            # Same key would mean the same unique_id as the Modbus entity.
            _LOGGER.warning(
                "Ignoring derived quantity '%s': key is already used by a data point",
                key,
            )
            continue
        sensors.append(AnkerSolixDerivedSensor(coordinator, key, definition, definitions))
    return sensors
