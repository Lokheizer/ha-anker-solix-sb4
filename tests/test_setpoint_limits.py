"""Tests for the fixed charge/discharge setpoint limits (fork, setpoint_limits.py).

Upstream issue #172: limiting the setpoint to the currently available charge
power keeps the battery heating from starting. The SB4 E5000 Pro uses fixed
limits per direction instead (3600 W charge, 2500 W discharge).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from custom_components.anker_solix_official.config_utils import (
    parse_device_configuration,
)
from custom_components.anker_solix_official.number import ModbusLocalDeviceNumber
from custom_components.anker_solix_official.setpoint_limits import (
    fixed_direction_max,
)

from ._fakes import FakeCoordinator, attach_entity_hass

SB4_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "anker_solix_official"
    / "config"
    / "58f0132b5f7979b2cfa43a0eb1fca770053288032386ff6a4da5ed2d72d4ea35.yaml"
)

LIMITS = {"charge": 3600, "discharge": 2500}


@pytest.fixture
def coord() -> FakeCoordinator:
    return FakeCoordinator()


def _config(**overrides):
    cfg = {"direction_entity": "dir", "direction_max_values": dict(LIMITS)}
    cfg.update(overrides)
    return cfg


class TestFixedDirectionMax:
    @pytest.mark.parametrize(("direction", "expected"), list(LIMITS.items()))
    def test_selected_direction_uses_its_limit(
        self, coord, direction, expected
    ) -> None:
        coord._user_selections["dir"] = direction
        assert fixed_direction_max(_config(), coord) == expected

    @pytest.mark.parametrize("direction", [None, "sideways"])
    def test_unknown_direction_uses_largest_limit(self, coord, direction) -> None:
        if direction is not None:
            coord._user_selections["dir"] = direction
        assert fixed_direction_max(_config(), coord) == 3600

    def test_without_direction_entity_uses_largest_limit(self, coord) -> None:
        coord._user_selections["dir"] = "discharge"
        cfg = _config()
        cfg.pop("direction_entity")
        assert fixed_direction_max(cfg, coord) == 3600

    @pytest.mark.parametrize("raw", [None, {}, [3600, 2500], "3600"])
    def test_missing_or_malformed_config_keeps_upstream_logic(
        self, coord, raw
    ) -> None:
        cfg = _config(direction_max_values=raw)
        if raw is None:
            cfg.pop("direction_max_values")
        assert fixed_direction_max(cfg, coord) is None

    def test_invalid_entries_are_ignored(self, coord) -> None:
        coord._user_selections["dir"] = "charge"
        cfg = _config(
            direction_max_values={
                "charge": "a lot",
                "discharge": 2500,
                "idle": 0,
                "boost": True,
            }
        )
        # The charge entry is invalid: fall back to the largest valid limit.
        assert fixed_direction_max(cfg, coord) == 2500

    def test_only_invalid_entries_keep_upstream_logic(self, coord) -> None:
        cfg = _config(direction_max_values={"charge": -1, "discharge": None})
        assert fixed_direction_max(cfg, coord) is None

    def test_never_below_min_value(self, coord) -> None:
        coord._user_selections["dir"] = "discharge"
        cfg = _config(min_value=3000)
        assert fixed_direction_max(cfg, coord) == 3000


def _make_setpoint(coord, **config) -> ModbusLocalDeviceNumber:
    cfg = {
        "address": 10071,
        "data_type": "INT32",
        "icon": "mdi:lightning-bolt",
        "unit": "W",
        "min_value": 0,
        "max_value": 10000,
        "direction_entity": "dir",
        "max_charge_power_entity": "mc",
        "max_discharge_power_entity": "md",
        "never_read_device": True,
        "direction_max_values": dict(LIMITS),
    }
    cfg.update(config)
    entity = ModbusLocalDeviceNumber(coord, "battery_power_setpoint", cfg)
    attach_entity_hass(entity, "number.battery_power_setpoint")
    return entity


class TestSetpointEntity:
    def test_charge_limit_ignores_available_power(self, coord) -> None:
        """Issue #172: only 1045 W available, 2500 W must still be allowed."""
        coord.data = {"mc": -1045, "md": 2500}
        coord._user_selections["dir"] = "charge"
        entity = _make_setpoint(coord)
        assert entity.native_max_value == 3600
        assert entity.max_value == 3600

    def test_discharge_limit_ignores_available_power(self, coord) -> None:
        coord.data = {"mc": -1045, "md": 800}
        coord._user_selections["dir"] = "discharge"
        entity = _make_setpoint(coord)
        assert entity.native_max_value == 2500
        assert entity.max_value == 2500

    def test_without_fixed_limits_upstream_dynamic_limit_applies(self, coord) -> None:
        coord.data = {"mc": -1045, "md": 2500}
        coord._user_selections["dir"] = "charge"
        entity = _make_setpoint(coord, direction_max_values=None)
        assert entity.native_max_value == 1045

    async def test_charge_above_available_power_is_written(self, coord) -> None:
        coord.data = {"mc": -1045, "md": 2500}
        coord._user_selections["dir"] = "charge"
        entity = _make_setpoint(coord)
        await entity.async_set_native_value(2500)
        coord.write_register.assert_awaited_once_with(10071, -2500, "INT32")
        assert coord._user_selections["battery_power_setpoint"] == 2500

    def test_attributes_show_fixed_limit_and_available_power(self, coord) -> None:
        coord.data = {"mc": -1045, "md": 2500}
        coord._user_selections["dir"] = "charge"
        attrs = _make_setpoint(coord).extra_state_attributes
        assert attrs["max_power"] == 3600
        assert attrs["max_charge_power"] == 1045
        assert attrs["max_discharge_power"] == 2500
        assert attrs["current_direction"] == "charge"


class TestSb4Config:
    @pytest.fixture
    def setpoint(self) -> dict:
        cfg = yaml.safe_load(SB4_CONFIG_PATH.read_text(encoding="utf-8"))
        data_points, _ = parse_device_configuration(cfg)
        return data_points["battery_power_setpoint"]

    def test_declares_fixed_limits(self, setpoint) -> None:
        assert setpoint["direction_max_values"] == LIMITS
        assert setpoint["direction_entity"] == "battery_power_direction"

    def test_sb4_setpoint_allows_full_charge_power(self, coord, setpoint) -> None:
        coord.data = {"max_charge_power": -1045, "max_discharge_power": 2500}
        coord._user_selections["battery_power_direction"] = "charge"
        entity = ModbusLocalDeviceNumber(coord, "battery_power_setpoint", setpoint)
        assert entity.native_max_value == 3600
        coord._user_selections["battery_power_direction"] = "discharge"
        assert entity.native_max_value == 2500


@pytest.mark.parametrize(
    "config_name",
    [
        "3c176e100f3c1d7a36ef64571e6aacd16b539f3657c11745f7bd969ccaf8b213.yaml",
        "482b2e95c3aaf78b871f463a71ba7f4fc87f9181872ac6168a90ad198fa3853a.yaml",
        "8fcbb87c685781b1d70d784a79eb923098955df2aaf199095ce7767bb70b913d.yaml",
    ],
)
def test_other_devices_keep_upstream_dynamic_limit(config_name: str) -> None:
    cfg = yaml.safe_load(
        (SB4_CONFIG_PATH.parent / config_name).read_text(encoding="utf-8")
    )
    data_points, _ = parse_device_configuration(cfg)
    assert "direction_max_values" not in data_points["battery_power_setpoint"]
