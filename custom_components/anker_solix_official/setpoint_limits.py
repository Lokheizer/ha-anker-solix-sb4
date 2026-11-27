"""Fixed charge/discharge limits for a direction-dependent power setpoint (fork).

Upstream limits the battery power setpoint to the power that is currently
available (``max_charge_power_entity`` / ``max_discharge_power_entity``). The
battery heating, however, only starts when more charge power is requested
than is available, so with that limit it never starts (upstream issue #172).

A device YAML can replace the dynamic limit with a fixed one per direction:

    battery_power_setpoint:
      direction_entity: "battery_power_direction"
      direction_max_values:
        charge: 3600
        discharge: 2500

Without ``direction_max_values`` the upstream behaviour is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def _valid_limits(config: dict[str, Any]) -> dict[str, float]:
    """Return the configured limits that are positive numbers."""
    raw = config.get("direction_max_values")
    if not isinstance(raw, dict):
        return {}

    limits: dict[str, float] = {}
    for direction, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            _LOGGER.debug(
                "Ignoring invalid direction_max_values entry %s=%r", direction, value
            )
            continue
        limits[str(direction)] = value
    return limits


def fixed_direction_max(config: dict[str, Any], coordinator: Any) -> float | None:
    """Return the fixed upper limit for the selected direction.

    Returns None if the entity has no valid ``direction_max_values``; the
    caller then keeps its own (upstream) logic. Without a known direction the
    largest configured limit applies. The result is never below ``min_value``.
    """
    limits = _valid_limits(config)
    if not limits:
        return None

    direction = None
    direction_entity = config.get("direction_entity")
    if direction_entity:
        direction = coordinator.get_user_selection(direction_entity)

    limit = limits.get(direction, max(limits.values()))
    return max(limit, config.get("min_value", 0))
