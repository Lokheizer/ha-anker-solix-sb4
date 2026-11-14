"""Tests for calculated sensors (derived_quantities, fork feature).

Covers the pure calculation, the entity, the sensor platform hook and the
shipped Solarbank 4 E5000 Pro configuration: MPPT input power PV1-3 is
voltage x current (registers 10167-10172), PV4 has no registers on that
device and is the remainder of the PCS PV power (register 10002). The
heating power is the power balance of the device: PCS PV power + battery power
(register 10008) - AC active power (register 10006).
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml
from custom_components.anker_solix_official.config_utils import (
    parse_device_configuration,
)
from custom_components.anker_solix_official.const import DOMAIN
from custom_components.anker_solix_official.derived_sensor import (
    AnkerSolixDerivedSensor,
    compute_derived_values,
    create_derived_sensors,
    get_derived_definitions,
)
from custom_components.anker_solix_official.modbus_client import (
    AnkerSolixModbusClient,
)
from custom_components.anker_solix_official.sensor import (
    ModbusLocalDeviceSensor,
    _is_sensor_entity,
    async_setup_entry,
)

from ._fakes import FakeCoordinator

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "anker_solix_official"
)
SB4_CONFIG_PATH = (
    COMPONENT_DIR
    / "config"
    / "58f0132b5f7979b2cfa43a0eb1fca770053288032386ff6a4da5ed2d72d4ea35.yaml"
)

PV_POWER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "pv1_power": {
        "translation_key": "pv1_power",
        "unit": "W",
        "device_class": "power",
        "state_class": "measurement",
        "icon": "mdi:solar-panel",
        "operation": "product",
        "sources": ["pv1_voltage", "pv1_current"],
        "min_value": 0,
        "precision": 0,
    },
    "pv2_power": {
        "unit": "W",
        "operation": "product",
        "sources": ["pv2_voltage", "pv2_current"],
        "min_value": 0,
        "precision": 0,
    },
    "pv_rest": {
        "unit": "W",
        "operation": "difference",
        "sources": ["pv_power", "pv1_power", "pv2_power"],
        "min_value": 0,
        "precision": 0,
    },
}

DAYLIGHT_FRAME = {
    "pv_power": 850,
    "pv1_voltage": 35.2,
    "pv1_current": 8.43,
    "pv2_voltage": 34.8,
    "pv2_current": 8.1,
}


class TestComputeDerivedValues:
    def test_product_multiplies_voltage_and_current(self) -> None:
        result = compute_derived_values(PV_POWER_DEFINITIONS, DAYLIGHT_FRAME)
        # 35.2 V x 8.43 A = 296.736 W, 34.8 V x 8.1 A = 281.88 W
        assert result["pv1_power"] == 297
        assert result["pv2_power"] == 282

    def test_precision_zero_returns_int(self) -> None:
        result = compute_derived_values(PV_POWER_DEFINITIONS, DAYLIGHT_FRAME)
        assert all(isinstance(value, int) for value in result.values())

    def test_difference_subtracts_following_sources_from_first(self) -> None:
        result = compute_derived_values(PV_POWER_DEFINITIONS, DAYLIGHT_FRAME)
        assert result["pv_rest"] == 850 - 297 - 282

    def test_chained_values_add_up_to_the_total(self) -> None:
        result = compute_derived_values(PV_POWER_DEFINITIONS, DAYLIGHT_FRAME)
        assert sum(result.values()) == DAYLIGHT_FRAME["pv_power"]

    def test_difference_is_clamped_to_min_value(self) -> None:
        # Total below the measured strings (tolerances): never a negative power.
        frame = {**DAYLIGHT_FRAME, "pv_power": 500}
        assert compute_derived_values(PV_POWER_DEFINITIONS, frame)["pv_rest"] == 0

    def test_negative_night_offsets_never_give_negative_power(self) -> None:
        frame = {
            "pv_power": 0,
            "pv1_voltage": 0.5,
            "pv1_current": -0.03,
            "pv2_voltage": -0.1,
            "pv2_current": -0.02,
        }
        assert compute_derived_values(PV_POWER_DEFINITIONS, frame) == {
            "pv1_power": 0,
            "pv2_power": 0,
            "pv_rest": 0,
        }

    def test_without_precision_keeps_full_resolution(self) -> None:
        definitions = {
            "p": {"operation": "product", "sources": ["pv1_voltage", "pv1_current"]}
        }
        value = compute_derived_values(definitions, DAYLIGHT_FRAME)["p"]
        assert value == pytest.approx(296.736)

    def test_precision_one_rounds_to_one_decimal(self) -> None:
        definitions = {
            "p": {
                "operation": "product",
                "sources": ["pv1_voltage", "pv1_current"],
                "precision": 1,
            }
        }
        assert compute_derived_values(definitions, DAYLIGHT_FRAME)["p"] == 296.7

    def test_without_min_value_negative_results_are_kept(self) -> None:
        definitions = {"d": {"operation": "difference", "sources": ["a", "b"]}}
        assert compute_derived_values(definitions, {"a": 1, "b": 3})["d"] == -2

    def test_missing_source_gives_none_not_zero(self) -> None:
        frame = {k: v for k, v in DAYLIGHT_FRAME.items() if k != "pv1_current"}
        result = compute_derived_values(PV_POWER_DEFINITIONS, frame)
        assert result["pv1_power"] is None
        assert result["pv2_power"] == 282
        # The remainder depends on pv1_power, so it is unknown as well.
        assert result["pv_rest"] is None

    @pytest.mark.parametrize("bad", [None, "8.43", True, math.nan, math.inf])
    def test_non_numeric_source_gives_none(self, bad: Any) -> None:
        frame = {**DAYLIGHT_FRAME, "pv1_current": bad}
        assert compute_derived_values(PV_POWER_DEFINITIONS, frame)["pv1_power"] is None

    @pytest.mark.parametrize("data", [None, {}])
    def test_empty_frame_gives_none_for_everything(self, data: Any) -> None:
        result = compute_derived_values(PV_POWER_DEFINITIONS, data)
        assert result == {"pv1_power": None, "pv2_power": None, "pv_rest": None}

    def test_no_definitions_gives_empty_result(self) -> None:
        assert compute_derived_values({}, DAYLIGHT_FRAME) == {}

    def test_balance_is_sum_of_sources_minus_sum_of_subtract(self) -> None:
        definitions = {
            "b": {"operation": "balance", "sources": ["a", "b"], "subtract": ["c", "d"]}
        }
        frame = {"a": 1000, "b": -600, "c": 300, "d": 50}
        assert compute_derived_values(definitions, frame)["b"] == 50

    def test_balance_without_subtract_is_a_plain_sum(self) -> None:
        definitions = {"s": {"operation": "balance", "sources": ["a", "b"]}}
        assert compute_derived_values(definitions, {"a": 2, "b": 3})["s"] == 5

    def test_balance_subtracting_a_negative_value_adds_it(self) -> None:
        definitions = {
            "b": {"operation": "balance", "sources": ["a"], "subtract": ["c"]}
        }
        assert compute_derived_values(definitions, {"a": -1000, "c": -1080})["b"] == 80

    def test_balance_can_use_an_earlier_derived_quantity(self) -> None:
        definitions = {
            **PV_POWER_DEFINITIONS,
            "b": {
                "operation": "balance",
                "sources": ["pv_power"],
                "subtract": ["pv1_power", "pv2_power"],
            },
        }
        assert compute_derived_values(definitions, DAYLIGHT_FRAME)["b"] == 271

    @pytest.mark.parametrize("missing", ["a", "c"])
    def test_balance_with_missing_value_gives_none_not_zero(self, missing: str) -> None:
        definitions = {
            "b": {"operation": "balance", "sources": ["a"], "subtract": ["c"]}
        }
        frame = {"a": 500, "c": 100}
        del frame[missing]
        assert compute_derived_values(definitions, frame)["b"] is None

    def test_balance_honours_min_value_and_precision(self) -> None:
        definitions = {
            "b": {
                "operation": "balance",
                "sources": ["a"],
                "subtract": ["c"],
                "min_value": 0,
                "precision": 0,
            }
        }
        assert compute_derived_values(definitions, {"a": 500, "c": 510})["b"] == 0
        value = compute_derived_values(definitions, {"a": 500.4, "c": 450})["b"]
        assert value == 50
        assert isinstance(value, int)


class TestGetDerivedDefinitions:
    @pytest.mark.parametrize(
        "config",
        [None, [], "x", {}, {"derived_quantities": None}, {"derived_quantities": []}],
    )
    def test_missing_or_malformed_section_gives_nothing(self, config: Any) -> None:
        assert get_derived_definitions(config) == {}

    def test_valid_definitions_are_returned_in_order(self) -> None:
        definitions = get_derived_definitions(
            {"derived_quantities": PV_POWER_DEFINITIONS}
        )
        assert list(definitions) == ["pv1_power", "pv2_power", "pv_rest"]

    def test_balance_with_and_without_subtract_is_valid(self) -> None:
        section = {
            "with": {"operation": "balance", "sources": ["a"], "subtract": ["c"]},
            "without": {"operation": "balance", "sources": ["a", "b"]},
        }
        definitions = get_derived_definitions({"derived_quantities": section})
        assert list(definitions) == ["with", "without"]

    @pytest.mark.parametrize(
        "definition",
        [
            "not a mapping",
            {"operation": "sum", "sources": ["a", "b"]},
            {"sources": ["a", "b"]},
            {"operation": "product"},
            {"operation": "product", "sources": []},
            {"operation": "product", "sources": "a"},
            {"operation": "product", "sources": ["a", 5]},
            {"operation": "balance", "sources": ["a"], "subtract": []},
            {"operation": "balance", "sources": ["a"], "subtract": "c"},
            {"operation": "balance", "sources": ["a"], "subtract": ["c", 5]},
            {"operation": "balance", "sources": ["a"], "subtract": None},
            {"operation": "difference", "sources": ["a", "b"], "subtract": ["c"]},
        ],
    )
    def test_invalid_definition_is_skipped_with_warning(
        self, definition: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        config = {
            "derived_quantities": {
                "broken": definition,
                "ok": {"operation": "product", "sources": ["a", "b"]},
            }
        }
        with caplog.at_level(logging.WARNING):
            definitions = get_derived_definitions(config)
        assert list(definitions) == ["ok"]
        assert "broken" in caplog.text


def _make_entity(
    coordinator: FakeCoordinator,
    key: str = "pv1_power",
    definitions: dict[str, dict[str, Any]] | None = None,
) -> AnkerSolixDerivedSensor:
    definitions = definitions if definitions is not None else PV_POWER_DEFINITIONS
    return AnkerSolixDerivedSensor(coordinator, key, definitions[key], definitions)


class TestDerivedSensorEntity:
    def test_declared_attributes(self) -> None:
        entity = _make_entity(FakeCoordinator())
        assert entity.native_unit_of_measurement == "W"
        assert entity.device_class == "power"
        assert entity.state_class == "measurement"
        assert entity.suggested_display_precision == 0
        assert entity.icon == "mdi:solar-panel"
        assert entity.translation_key == "pv1_power"
        assert entity.unique_id == "test-entry_pv1_power"
        assert entity.has_entity_name is True

    def test_translation_key_defaults_to_entity_key(self) -> None:
        entity = _make_entity(FakeCoordinator(), "pv2_power")
        assert entity.translation_key == "pv2_power"

    def test_undeclared_classes_stay_unset(self) -> None:
        entity = _make_entity(FakeCoordinator(), "pv2_power")
        assert entity.device_class is None
        assert entity.state_class is None

    def test_slash_unit_means_no_unit(self) -> None:
        definitions = {"r": {"unit": "/", "operation": "product", "sources": ["a"]}}
        entity = _make_entity(FakeCoordinator(), "r", definitions)
        assert entity.native_unit_of_measurement is None
        assert entity.suggested_display_precision is None

    def test_invalid_declared_class_is_ignored_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        definitions = {
            "p": {
                "device_class": "not_a_class",
                "state_class": "none",
                "operation": "product",
                "sources": ["a"],
            }
        }
        with caplog.at_level(logging.WARNING):
            entity = _make_entity(FakeCoordinator(), "p", definitions)
        assert entity.device_class is None
        assert entity.state_class is None
        assert "not_a_class" in caplog.text

    def test_native_value_is_calculated_from_coordinator_data(self) -> None:
        coordinator = FakeCoordinator(dict(DAYLIGHT_FRAME))
        assert _make_entity(coordinator, "pv1_power").native_value == 297
        assert _make_entity(coordinator, "pv_rest").native_value == 271

    def test_native_value_follows_new_frames(self) -> None:
        coordinator = FakeCoordinator(dict(DAYLIGHT_FRAME))
        entity = _make_entity(coordinator)
        coordinator.data = {**DAYLIGHT_FRAME, "pv1_current": 4.0}
        assert entity.native_value == 141

    def test_missing_source_is_unknown_but_available(self) -> None:
        coordinator = FakeCoordinator({"pv1_voltage": 35.2})
        entity = _make_entity(coordinator)
        assert entity.available is True
        assert entity.native_value is None

    def test_none_and_unavailable_when_coordinator_failed(self) -> None:
        coordinator = FakeCoordinator(dict(DAYLIGHT_FRAME))
        coordinator.last_update_success = False
        entity = _make_entity(coordinator)
        assert entity.available is False
        assert entity.native_value is None

    def test_no_data_yet_is_unknown(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = None
        assert _make_entity(coordinator).native_value is None

    def test_attributes_describe_the_calculation(self) -> None:
        entity = _make_entity(FakeCoordinator(), "pv_rest")
        assert entity.extra_state_attributes == {
            "operation": "difference",
            "sources": ["pv_power", "pv1_power", "pv2_power"],
        }

    def test_attributes_of_a_balance_list_what_is_subtracted(self) -> None:
        definitions = {
            "b": {"operation": "balance", "sources": ["a", "b"], "subtract": ["c"]}
        }
        entity = _make_entity(FakeCoordinator(), "b", definitions)
        assert entity.extra_state_attributes == {
            "operation": "balance",
            "sources": ["a", "b"],
            "subtract": ["c"],
        }


class TestCreateDerivedSensors:
    def test_coordinator_without_config_cache_creates_nothing(self) -> None:
        assert create_derived_sensors(SimpleNamespace()) == []

    def test_config_without_section_creates_nothing(self) -> None:
        coordinator = FakeCoordinator()
        coordinator._full_config_cache = {"read_quantities": {}}
        assert create_derived_sensors(coordinator) == []

    def test_creates_one_sensor_per_definition(self) -> None:
        coordinator = FakeCoordinator(dict(DAYLIGHT_FRAME))
        coordinator._full_config_cache = {"derived_quantities": PV_POWER_DEFINITIONS}
        sensors = create_derived_sensors(coordinator)
        assert [s.unique_id for s in sensors] == [
            "test-entry_pv1_power",
            "test-entry_pv2_power",
            "test-entry_pv_rest",
        ]
        assert [s.native_value for s in sensors] == [297, 282, 271]

    def test_key_of_existing_data_point_is_skipped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = FakeCoordinator()
        coordinator._full_config_cache = {"derived_quantities": PV_POWER_DEFINITIONS}
        coordinator._device_config_cache = {"pv1_power": {"address": 1}}
        with caplog.at_level(logging.WARNING):
            sensors = create_derived_sensors(coordinator)
        assert [s.unique_id for s in sensors] == [
            "test-entry_pv2_power",
            "test-entry_pv_rest",
        ]
        assert "pv1_power" in caplog.text


class TestSensorPlatformHook:
    """sensor.async_setup_entry must add the calculated sensors as well."""

    async def test_adds_modbus_and_derived_sensors(self) -> None:
        coordinator = FakeCoordinator(dict(DAYLIGHT_FRAME))
        data_points = {
            "pv_power": {
                "address": 10002,
                "data_type": "INT32",
                "unit": "W",
                "gain": 1,
            },
            "pv1_voltage": {"address": 10167, "data_type": "INT16", "internal": True},
        }
        coordinator.get_device_data_points = AsyncMock(return_value=data_points)
        coordinator._device_config_cache = data_points
        coordinator._full_config_cache = {"derived_quantities": PV_POWER_DEFINITIONS}
        hass = SimpleNamespace(data={DOMAIN: {"e1": coordinator}})
        added: list = []

        await async_setup_entry(hass, SimpleNamespace(entry_id="e1"), added.extend)

        assert [type(e) for e in added] == [
            ModbusLocalDeviceSensor,
            AnkerSolixDerivedSensor,
            AnkerSolixDerivedSensor,
            AnkerSolixDerivedSensor,
        ]

    async def test_device_without_section_gets_no_extra_sensors(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.get_device_data_points = AsyncMock(
            return_value={"pv_power": {"address": 10002, "data_type": "INT32"}}
        )
        coordinator._full_config_cache = {"read_quantities": {}}
        hass = SimpleNamespace(data={DOMAIN: {"e1": coordinator}})
        added: list = []

        await async_setup_entry(hass, SimpleNamespace(entry_id="e1"), added.extend)

        assert [type(e) for e in added] == [ModbusLocalDeviceSensor]


# --- Shipped Solarbank 4 E5000 Pro configuration ---------------------------

# key: (address, data_type, unit, gain, count), from the register sheet
SB4_PV_STRING_DATA_POINTS = {
    "pv1_voltage": (10167, "INT16", "V", 10, 1),
    "pv1_current": (10168, "INT16", "A", 100, 1),
    "pv2_voltage": (10169, "INT16", "V", 10, 1),
    "pv2_current": (10170, "INT16", "A", 100, 1),
    "pv3_voltage": (10171, "INT16", "V", 10, 1),
    "pv3_current": (10172, "INT16", "A", 100, 1),
}
SB4_PV_POWER_KEYS = ["pv1_power", "pv2_power", "pv3_power", "pv4_power"]
SB4_DERIVED_KEYS = [*SB4_PV_POWER_KEYS, "heating_power"]
# Signed powers behind the heating power: (address, data_type, unit, gain, count)
SB4_POWER_BALANCE_DATA_POINTS = {
    "active_power": (10006, "INT32", "W", 1, 2),
    "battery_power": (10008, "INT32", "W", 1, 2),
}
# X1-style PV4 voltage/current: not implemented on the SB4, must never be read.
SB4_UNIMPLEMENTED_PV4_REGISTERS = (10173, 10174)


def _load_sb4_config() -> dict[str, Any]:
    return yaml.safe_load(SB4_CONFIG_PATH.read_text(encoding="utf-8"))


class TestShippedSb4Config:
    def test_pv_string_data_points_match_register_sheet(self) -> None:
        data_points, _ = parse_device_configuration(_load_sb4_config())
        for key, expected in SB4_PV_STRING_DATA_POINTS.items():
            dp = data_points[key]
            actual = (
                dp["address"],
                dp["data_type"],
                dp["unit"],
                dp["gain"],
                dp["count"],
            )
            assert actual == expected, key

    def test_pv_string_data_points_create_no_entities(self) -> None:
        data_points, _ = parse_device_configuration(_load_sb4_config())
        for key in SB4_PV_STRING_DATA_POINTS:
            assert not _is_sensor_entity(key, data_points[key]), key

    def test_pv_string_data_points_sit_inside_an_input_batch_range(self) -> None:
        data_points, batch_ranges = parse_device_configuration(_load_sb4_config())
        for key in SB4_PV_STRING_DATA_POINTS:
            address = data_points[key]["address"]
            assert any(
                start <= address <= end and reg_type == "input"
                for start, end, reg_type in batch_ranges
            ), key

    def test_no_batch_range_touches_the_unimplemented_pv4_registers(self) -> None:
        _, batch_ranges = parse_device_configuration(_load_sb4_config())
        for start, end, _ in batch_ranges:
            for address in SB4_UNIMPLEMENTED_PV4_REGISTERS:
                assert not start <= address <= end, (start, end)

    def test_no_data_point_uses_the_unimplemented_pv4_registers(self) -> None:
        data_points, _ = parse_device_configuration(_load_sb4_config())
        for key, dp in data_points.items():
            last = dp["address"] + dp.get("count", 1) - 1
            for address in SB4_UNIMPLEMENTED_PV4_REGISTERS:
                assert not dp["address"] <= address <= last, key

    def test_derived_sensors_are_declared_as_power_in_watt(self) -> None:
        definitions = get_derived_definitions(_load_sb4_config())
        assert list(definitions) == SB4_DERIVED_KEYS
        for key, definition in definitions.items():
            assert definition["unit"] == "W", key
            assert definition["device_class"] == "power", key
            assert definition["state_class"] == "measurement", key
            assert definition["min_value"] == 0, key
            assert definition["precision"] == 0, key

    def test_pv1_to_pv3_are_voltage_times_current(self) -> None:
        definitions = get_derived_definitions(_load_sb4_config())
        for n in (1, 2, 3):
            definition = definitions[f"pv{n}_power"]
            assert definition["operation"] == "product"
            assert definition["sources"] == [f"pv{n}_voltage", f"pv{n}_current"]

    def test_pv4_is_total_pv_power_minus_pv1_to_pv3(self) -> None:
        cfg = _load_sb4_config()
        definition = get_derived_definitions(cfg)["pv4_power"]
        assert definition["operation"] == "difference"
        assert definition["sources"] == [
            "pv_power",
            "pv1_power",
            "pv2_power",
            "pv3_power",
        ]
        # The total is the PCS PV power register, not third-party PV.
        assert cfg["read_quantities"]["pv_power"]["address"] == 10002

    def test_heating_power_is_pv_plus_battery_minus_ac_active_power(self) -> None:
        cfg = _load_sb4_config()
        definition = get_derived_definitions(cfg)["heating_power"]
        assert definition["operation"] == "balance"
        assert definition["sources"] == ["pv_power", "battery_power"]
        assert definition["subtract"] == ["active_power"]
        # PCS PV power only: third-party PV does not pass through the device.
        assert cfg["read_quantities"]["pv_power"]["address"] == 10002

    def test_power_balance_data_points_match_register_sheet(self) -> None:
        data_points, _ = parse_device_configuration(_load_sb4_config())
        for key, expected in SB4_POWER_BALANCE_DATA_POINTS.items():
            dp = data_points[key]
            actual = (
                dp["address"],
                dp["data_type"],
                dp["unit"],
                dp["gain"],
                dp["count"],
            )
            assert actual == expected, key

    def test_power_balance_data_points_create_no_entities(self) -> None:
        data_points, _ = parse_device_configuration(_load_sb4_config())
        for key in SB4_POWER_BALANCE_DATA_POINTS:
            assert not _is_sensor_entity(key, data_points[key]), key

    def test_power_balance_data_points_sit_inside_an_input_batch_range(self) -> None:
        data_points, batch_ranges = parse_device_configuration(_load_sb4_config())
        for key in SB4_POWER_BALANCE_DATA_POINTS:
            first = data_points[key]["address"]
            last = first + data_points[key]["count"] - 1
            assert any(
                start <= first and last <= end and reg_type == "input"
                for start, end, reg_type in batch_ranges
            ), key

    def test_every_source_is_a_data_point_or_an_earlier_derived_quantity(
        self,
    ) -> None:
        cfg = _load_sb4_config()
        data_points, _ = parse_device_configuration(cfg)
        known = set(data_points)
        for key, definition in get_derived_definitions(cfg).items():
            for source in definition["sources"] + definition.get("subtract", []):
                assert source in known, (key, source)
            known.add(key)

    def test_derived_keys_do_not_collide_with_data_points(self) -> None:
        cfg = _load_sb4_config()
        data_points, _ = parse_device_configuration(cfg)
        assert not set(get_derived_definitions(cfg)) & set(data_points)

    def test_every_translation_key_exists_in_all_languages(self) -> None:
        lang_files = [COMPONENT_DIR / "strings.json"] + [
            COMPONENT_DIR / "translations" / f"{code}.json"
            for code in ("en", "de", "fr", "nl")
        ]
        definitions = get_derived_definitions(_load_sb4_config())
        for lang_file in lang_files:
            sensors = json.loads(lang_file.read_text(encoding="utf-8"))["entity"][
                "sensor"
            ]
            for key, definition in definitions.items():
                translation_key = definition.get("translation_key", key)
                assert sensors.get(translation_key, {}).get("name"), (
                    lang_file.name,
                    key,
                )

    def test_only_the_sb4_config_declares_derived_quantities(self) -> None:
        for path in sorted((COMPONENT_DIR / "config").glob("*.yaml")):
            cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
            if path == SB4_CONFIG_PATH:
                assert "derived_quantities" in cfg
            else:
                assert "derived_quantities" not in cfg, path.name


# --- Read path with the shipped config against a simulated device ----------


class _Response:
    def __init__(self, registers: list[int] | None = None, error: bool = False) -> None:
        self.registers = registers
        self._error = error

    def isError(self) -> bool:  # same casing as the pymodbus response API
        return self._error


class _SimulatedSb4:
    """Stand-in for the pymodbus client, answering from a register map.

    Like the real firmware it rejects a read that starts at an unimplemented
    PV4 register and pads unimplemented registers inside a batch with zeros.
    """

    def __init__(self, registers: dict[int, int]) -> None:
        self._registers = registers
        self.connected = True
        self.input_requests: list[tuple[int, int]] = []
        self.holding_requests: list[tuple[int, int]] = []

    def _answer(self, address: int, count: int) -> _Response:
        if address in SB4_UNIMPLEMENTED_PV4_REGISTERS:
            return _Response(error=True)
        return _Response(
            [self._registers.get(a, 0) for a in range(address, address + count)]
        )

    async def read_input_registers(self, address: int, count: int) -> _Response:
        self.input_requests.append((address, count))
        return self._answer(address, count)

    async def read_holding_registers(self, address: int, count: int) -> _Response:
        self.holding_requests.append((address, count))
        return self._answer(address, count)


async def _read_sb4_frame(registers: dict[int, int]) -> tuple[dict, _SimulatedSb4]:
    device = _SimulatedSb4(registers)
    client = AnkerSolixModbusClient(ip_address="127.0.0.1", port=502)
    client.client = device
    client._connection_status = "connected"
    data_points, batch_ranges = parse_device_configuration(_load_sb4_config())
    data = await client.get_all_data(data_points, batch_ranges)
    return data, device


SB4_DAYLIGHT_REGISTERS = {
    10003: 850,  # PCS PV power 850 W (INT32, low word)
    10005: 120,  # third-party PV 120 W, must not influence PV4
    10167: 352,  # PV1 35.2 V
    10168: 843,  # PV1 8.43 A
    10169: 348,  # PV2 34.8 V
    10170: 810,  # PV2 8.10 A
    10171: 0xFFFF,  # PV3 -0.1 V (idle input, negative ADC offset)
    10172: 0xFFFE,  # PV3 -0.02 A
}


class TestSb4ReadPath:
    async def test_pv_strings_are_read_as_one_block_ending_at_10172(self) -> None:
        _, device = await _read_sb4_frame(SB4_DAYLIGHT_REGISTERS)
        assert (10167, 6) in device.input_requests

    async def test_unimplemented_pv4_registers_are_never_requested(self) -> None:
        _, device = await _read_sb4_frame(SB4_DAYLIGHT_REGISTERS)
        for address, count in device.input_requests + device.holding_requests:
            for pv4 in SB4_UNIMPLEMENTED_PV4_REGISTERS:
                assert not address <= pv4 < address + count, (address, count)

    async def test_voltage_and_current_are_decoded_signed_and_scaled(self) -> None:
        data, _ = await _read_sb4_frame(SB4_DAYLIGHT_REGISTERS)
        assert data["pv1_voltage"] == pytest.approx(35.2)
        assert data["pv1_current"] == pytest.approx(8.43)
        assert data["pv2_voltage"] == pytest.approx(34.8)
        assert data["pv2_current"] == pytest.approx(8.1)
        assert data["pv3_voltage"] == pytest.approx(-0.1)
        assert data["pv3_current"] == pytest.approx(-0.02)

    async def test_pv_powers_from_a_real_frame(self) -> None:
        data, _ = await _read_sb4_frame(SB4_DAYLIGHT_REGISTERS)
        cfg = _load_sb4_config()
        coordinator = FakeCoordinator(data)
        coordinator._full_config_cache = cfg
        coordinator._device_config_cache, _ = parse_device_configuration(cfg)

        values = {
            sensor.translation_key: sensor.native_value
            for sensor in create_derived_sensors(coordinator)
            if sensor.translation_key in SB4_PV_POWER_KEYS
        }

        assert values == {
            "pv1_power": 297,
            "pv2_power": 282,
            "pv3_power": 0,
            "pv4_power": 271,
        }
        assert sum(values.values()) == data["pv_power"] == 850

    async def test_pv4_is_zero_when_strings_exceed_the_total(self) -> None:
        data, _ = await _read_sb4_frame({**SB4_DAYLIGHT_REGISTERS, 10003: 570})
        values = compute_derived_values(
            get_derived_definitions(_load_sb4_config()), data
        )
        assert values["pv4_power"] == 0

    async def test_all_zero_at_night(self) -> None:
        data, _ = await _read_sb4_frame({10167: 0xFFFF, 10168: 0xFFFE})
        values = compute_derived_values(
            get_derived_definitions(_load_sb4_config()), data
        )
        assert values == dict.fromkeys(SB4_DERIVED_KEYS, 0)


def _int32(value: int, address: int) -> dict[int, int]:
    """Registers of a signed 32-bit value (high word first, two's complement)."""
    raw = value & 0xFFFFFFFF
    return {address: raw >> 16, address + 1: raw & 0xFFFF}


def _sb4_power_registers(pv: int, battery: int, active: int) -> dict[int, int]:
    """PCS PV power, battery power (+ discharging) and AC active power (+ output)."""
    return {
        **_int32(pv, 10002),
        **_int32(active, 10006),
        **_int32(battery, 10008),
    }


class TestSb4HeatingPower:
    async def _heating_power(self, registers: dict[int, int]) -> Any:
        data, _ = await _read_sb4_frame(registers)
        values = compute_derived_values(
            get_derived_definitions(_load_sb4_config()), data
        )
        return values["heating_power"]

    async def test_signed_powers_are_decoded(self) -> None:
        data, _ = await _read_sb4_frame(
            _sb4_power_registers(pv=0, battery=-1000, active=-1080)
        )
        assert data["active_power"] == -1080
        assert data["battery_power"] == -1000

    async def test_needs_no_request_beyond_the_existing_batch_ranges(self) -> None:
        _, device = await _read_sb4_frame(_sb4_power_registers(0, -1000, -1080))
        assert (10000, 51) in device.input_requests
        for address, count in device.input_requests:
            if address != 10000:
                assert not address <= 10009 < address + count, (address, count)
                assert not 10006 <= address <= 10009, (address, count)

    async def test_pv_feeds_house_and_battery(self) -> None:
        # 1000 W in from PV, 600 W into the battery, 350 W out via AC.
        registers = _sb4_power_registers(pv=1000, battery=-600, active=350)
        assert await self._heating_power(registers) == 50

    async def test_charging_from_the_grid(self) -> None:
        # 1080 W in from the grid (negative active power), 1000 W into the battery.
        registers = _sb4_power_registers(pv=0, battery=-1000, active=-1080)
        assert await self._heating_power(registers) == 80

    async def test_grid_and_pv_charge_the_battery_together(self) -> None:
        registers = _sb4_power_registers(pv=400, battery=-1500, active=-1190)
        assert await self._heating_power(registers) == 90

    async def test_discharging_into_the_house(self) -> None:
        # 800 W in from the battery, 740 W out via AC.
        registers = _sb4_power_registers(pv=0, battery=800, active=740)
        assert await self._heating_power(registers) == 60

    async def test_standby_draw_from_the_grid(self) -> None:
        registers = _sb4_power_registers(pv=0, battery=0, active=-12)
        assert await self._heating_power(registers) == 12

    async def test_never_negative_when_registers_are_out_of_step(self) -> None:
        # Output already jumped, PV value still from the previous moment.
        registers = _sb4_power_registers(pv=500, battery=0, active=510)
        assert await self._heating_power(registers) == 0

    async def test_third_party_pv_is_not_part_of_the_balance(self) -> None:
        registers = {
            **_sb4_power_registers(pv=1000, battery=-600, active=350),
            **_int32(400, 10004),
        }
        assert await self._heating_power(registers) == 50

    async def test_sensor_entity_reports_the_heating_power(self) -> None:
        data, _ = await _read_sb4_frame(
            _sb4_power_registers(pv=1000, battery=-600, active=350)
        )
        cfg = _load_sb4_config()
        coordinator = FakeCoordinator(data)
        coordinator._full_config_cache = cfg
        coordinator._device_config_cache, _ = parse_device_configuration(cfg)

        sensor = next(
            s
            for s in create_derived_sensors(coordinator)
            if s.translation_key == "heating_power"
        )

        assert sensor.native_value == 50
        assert sensor.native_unit_of_measurement == "W"
        assert sensor.device_class == "power"
        assert sensor.state_class == "measurement"
        assert sensor.unique_id == "test-entry_heating_power"
