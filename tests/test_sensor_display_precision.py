"""Fork: ``display_precision`` in a device YAML overrides the gain-derived value."""

from __future__ import annotations

from typing import Any

import pytest
from custom_components.anker_solix_official.sensor import ModbusLocalDeviceSensor

BASE_CONFIG = {
    "address": 10156,
    "data_type": "INT16",
    "unit": "°C",
    "gain": 10,
    "count": 1,
}


class _FakeCoordinator:
    """Stand-in exposing only what ModbusLocalDeviceSensor reads."""

    def __init__(self) -> None:
        self.entry = type("Entry", (), {"entry_id": "test-entry"})()
        self.device_info = {"model": "Solarbank 4 E5000 Pro"}
        self.data: dict[str, Any] = {}
        self.last_update_success = True

    def is_connected(self) -> bool:
        return self.last_update_success

    def is_register_available(self, address: int) -> bool:
        return True

    def get_protected_value(self, entity_key: str) -> tuple[bool, Any]:
        return False, None

    def async_add_listener(self, listener):
        return lambda: None


def _make(config: dict[str, Any]) -> ModbusLocalDeviceSensor:
    return ModbusLocalDeviceSensor(_FakeCoordinator(), "temperature", config)


def test_gain_derives_precision_without_declaration() -> None:
    assert _make(dict(BASE_CONFIG)).suggested_display_precision == 1


def test_declared_zero_overrides_gain() -> None:
    entity = _make({**BASE_CONFIG, "display_precision": 0})
    assert entity.suggested_display_precision == 0


def test_declared_value_sets_precision_without_gain_decimals() -> None:
    entity = _make({**BASE_CONFIG, "gain": 1, "display_precision": 2})
    assert entity.suggested_display_precision == 2


@pytest.mark.parametrize("raw", ["1", True, -1, 1.5, None])
def test_invalid_declaration_keeps_gain_derived_precision(raw: Any) -> None:
    entity = _make({**BASE_CONFIG, "display_precision": raw})
    assert entity.suggested_display_precision == 1
