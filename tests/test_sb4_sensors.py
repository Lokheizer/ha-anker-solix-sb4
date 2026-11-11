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


# key -> (address, data_type, unit, gain, count), per Anker SOLIX Modbus
# Protocol V1.0.0, "Summary - Information".
PLANT_SUMMARY_SENSORS = {
    "power_station_status": (10000, "UINT16", "/", 1, 1),
    "battery_soh": (10015, "UINT16", "%", 1, 1),
}


def _lang_files() -> list[Path]:
    base = SB4_CONFIG_PATH.parents[1]
    return [base / "strings.json", *sorted((base / "translations").glob("*.json"))]


def _make_entity(key: str, value: Any) -> ModbusLocalDeviceSensor:
    data_points, _ = _load_sb4_config()
    coordinator = _FakeCoordinator()
    coordinator.data[key] = value
    return ModbusLocalDeviceSensor(coordinator, key, data_points[key])


@pytest.mark.parametrize("key", sorted(PLANT_SUMMARY_SENSORS))
class TestPlantSummarySensors:
    def test_matches_protocol_sheet(self, key: str) -> None:
        data_points, _ = _load_sb4_config()
        dp = data_points[key]
        actual = (dp["address"], dp["data_type"], dp["unit"], dp["gain"], dp["count"])
        assert actual == PLANT_SUMMARY_SENSORS[key]

    def test_is_exposed_as_sensor(self, key: str) -> None:
        data_points, _ = _load_sb4_config()
        assert _is_sensor_entity(key, data_points[key])

    def test_sits_inside_a_batch_range(self, key: str) -> None:
        data_points, batch_ranges = _load_sb4_config()
        dp = data_points[key]
        last = dp["address"] + dp["count"] - 1
        assert any(
            start <= dp["address"] and last <= end for start, end, _ in batch_ranges
        )

    def test_translation_exists_in_all_languages(self, key: str) -> None:
        for lang_file in _lang_files():
            sensors = json.loads(lang_file.read_text(encoding="utf-8"))["entity"][
                "sensor"
            ]
            assert sensors.get(key, {}).get("name"), lang_file.name


def test_plant_status_is_an_enum_with_translated_states() -> None:
    data_points, _ = _load_sb4_config()
    mapping = data_points["power_station_status"]["value_mapping"]
    assert mapping == {
        0: "unknown",
        1: "grid_tied",
        2: "off_grid",
        3: "standby",
        4: "fault",
    }
    entity = _make_entity("power_station_status", 1)
    assert entity.device_class == SensorDeviceClass.ENUM
    assert entity.native_value == "grid_tied"
    for lang_file in _lang_files():
        states = json.loads(lang_file.read_text(encoding="utf-8"))["entity"]["sensor"][
            "power_station_status"
        ]["state"]
        assert set(mapping.values()) <= set(states), lang_file.name


def test_soh_is_a_plain_percentage_not_a_battery_level() -> None:
    entity = _make_entity("battery_soh", 98)
    assert entity.device_class is None
    assert entity.state_class == SensorStateClass.MEASUREMENT
    assert entity.native_unit_of_measurement == "%"
    assert entity.native_value == 98


@pytest.mark.parametrize(
    "key", ["total_battery_charge_energy", "total_battery_discharge_energy"]
)
def test_redundant_battery_energy_totals_are_not_defined(key: str) -> None:
    """10022/10024 mirror 10262/10264 on the SB4 (verified on the device)."""
    data_points, _ = _load_sb4_config()
    assert key not in data_points
    assert {10022, 10024}.isdisjoint(dp.get("address") for dp in data_points.values())
