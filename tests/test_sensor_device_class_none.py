"""Fork: ``device_class: none`` in a device YAML removes the inferred class."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from custom_components.anker_solix_official.sensor import ModbusLocalDeviceSensor
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

BASE_CONFIG = {
    "address": 10015,
    "data_type": "UINT16",
    "unit": "%",
    "gain": 1,
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
    return ModbusLocalDeviceSensor(_FakeCoordinator(), "soh", config)


def test_percentage_infers_battery_without_declaration() -> None:
    assert _make(dict(BASE_CONFIG)).device_class == SensorDeviceClass.BATTERY


@pytest.mark.parametrize("raw", ["none", "None", "NONE", None])
def test_explicit_none_removes_inferred_device_class(
    raw: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        entity = _make({**BASE_CONFIG, "device_class": raw})
    assert entity.device_class is None
    assert entity.state_class == SensorStateClass.MEASUREMENT
    assert entity.native_unit_of_measurement == "%"
    assert "Ignoring invalid" not in caplog.text


def test_declared_device_class_still_overrides() -> None:
    entity = _make({**BASE_CONFIG, "device_class": "power_factor"})
    assert entity.device_class == SensorDeviceClass.POWER_FACTOR
