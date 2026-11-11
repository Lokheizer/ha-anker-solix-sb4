"""Consistency tests for the fork's Solarbank 4 E5000 Pro sensors.

Fork feature: registers 10036 (Rechargeable Power) and 10038 (Dischargeable
Power) are exposed as sensors instead of being internal-only data points.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from custom_components.anker_solix_official.config_utils import (
    parse_device_configuration,
)
from custom_components.anker_solix_official.sensor import (
    ModbusLocalDeviceSensor,
    _is_sensor_entity,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

SB4_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "anker_solix_official"
    / "config"
    / "58f0132b5f7979b2cfa43a0eb1fca770053288032386ff6a4da5ed2d72d4ea35.yaml"
)

# key -> (address, data_type, unit, gain, count), per Anker SOLIX Modbus
# Protocol V1.0.0, "Summary - Information".
AVAILABLE_POWER_SENSORS = {
    "max_charge_power": (10036, "INT32", "W", 1, 2),
    "max_discharge_power": (10038, "INT32", "W", 1, 2),
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


def _load_sb4_config():
    cfg = yaml.safe_load(SB4_CONFIG_PATH.read_text(encoding="utf-8"))
    return parse_device_configuration(cfg)


@pytest.mark.parametrize("key", sorted(AVAILABLE_POWER_SENSORS))
class TestAvailablePowerSensors:
    def test_matches_protocol_sheet(self, key: str) -> None:
        data_points, _ = _load_sb4_config()
        dp = data_points[key]
        actual = (dp["address"], dp["data_type"], dp["unit"], dp["gain"], dp["count"])
        assert actual == AVAILABLE_POWER_SENSORS[key]

    def test_is_exposed_as_sensor(self, key: str) -> None:
        data_points, _ = _load_sb4_config()
        assert not data_points[key].get("internal")
        assert _is_sensor_entity(key, data_points[key])

    def test_sits_inside_a_batch_range(self, key: str) -> None:
        data_points, batch_ranges = _load_sb4_config()
        dp = data_points[key]
        last = dp["address"] + dp["count"] - 1
        assert any(
            start <= dp["address"] and last <= end for start, end, _ in batch_ranges
        )

    def test_entity_is_a_power_measurement(self, key: str) -> None:
        data_points, _ = _load_sb4_config()
        coordinator = _FakeCoordinator()
        coordinator.data[key] = 1200
        entity = ModbusLocalDeviceSensor(coordinator, key, data_points[key])
        assert entity.unique_id == f"test-entry_{key}"
        assert entity.device_class == SensorDeviceClass.POWER
        assert entity.state_class == SensorStateClass.MEASUREMENT
        assert entity.native_unit_of_measurement == "W"
        assert entity.native_value == 1200

    def test_translation_exists_in_all_languages(self, key: str) -> None:
        base = SB4_CONFIG_PATH.parents[1]
        lang_files = [
            base / "strings.json",
            *sorted((base / "translations").glob("*.json")),
        ]
        data_points, _ = _load_sb4_config()
        translation_key = data_points[key].get("translation_key", key)
        for lang_file in lang_files:
            sensors = json.loads(lang_file.read_text(encoding="utf-8"))["entity"][
                "sensor"
            ]
            assert sensors.get(translation_key, {}).get("name"), lang_file.name


def test_setpoint_still_uses_available_power_limits() -> None:
    """The number entity keeps reading its limits from the same data points."""
    data_points, _ = _load_sb4_config()
    setpoint = data_points["battery_power_setpoint"]
    assert setpoint["max_charge_power_entity"] == "max_charge_power"
    assert setpoint["max_discharge_power_entity"] == "max_discharge_power"
