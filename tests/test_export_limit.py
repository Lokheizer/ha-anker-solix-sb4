"""Tests for the fork's export limitation controls (export_limit.py).

Fork feature for the Solarbank 4 E5000 Pro: registers 10074 (Export Power
Limitation Enable) and 10075/10076 (Export Power Limit Control Value) are
combined into a select with the three modes of the Anker app and a number with
the custom limit.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from custom_components.anker_solix_official import number as number_platform
from custom_components.anker_solix_official import select as select_platform
from custom_components.anker_solix_official.config_utils import (
    parse_device_configuration,
)
from custom_components.anker_solix_official.const import DOMAIN
from custom_components.anker_solix_official.export_limit import (
    MODE_CUSTOM,
    MODE_UNLIMITED,
    MODE_ZERO_EXPORT,
    MODES,
    NUMBER_KEY,
    SELECT_KEY,
    AnkerSolixExportLimitNumber,
    AnkerSolixExportLimitSelect,
    ExportLimitController,
    create_export_limit_numbers,
    create_export_limit_selects,
    get_export_limit_config,
    get_export_limit_controller,
    resolve_mode,
)
from custom_components.anker_solix_official.sensor import _is_sensor_entity
from homeassistant.exceptions import HomeAssistantError

from ._fakes import FakeCoordinator, attach_entity_hass, make_write_result

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "anker_solix_official"
)
SB4_CONFIG_PATH = (
    COMPONENT_DIR
    / "config"
    / "58f0132b5f7979b2cfa43a0eb1fca770053288032386ff6a4da5ed2d72d4ea35.yaml"
)

# key -> (address, data_type, count), per Anker SOLIX Modbus Protocol V1.0.0,
# "Summary - Control".
RAW_DATA_POINTS = {
    "export_limit_enable": (10074, "UINT16", 1),
    "export_limit_value": (10075, "INT32", 2),
}

ENABLE = "export_limit_enable"
VALUE = "export_limit_value"
# INT32 -1 = 0xFFFF 0xFFFF, read from the device with the app set to "unlimited".
UNLIMITED = -1


def _load_sb4() -> tuple[dict[str, Any], dict[str, Any], list]:
    full_config = yaml.safe_load(SB4_CONFIG_PATH.read_text(encoding="utf-8"))
    data_points, batch_ranges = parse_device_configuration(full_config)
    return full_config, data_points, batch_ranges


def _lang_files() -> list[Path]:
    return [
        COMPONENT_DIR / "strings.json",
        *sorted((COMPONENT_DIR / "translations").glob("*.json")),
    ]


@pytest.fixture
def coord() -> FakeCoordinator:
    """Fake coordinator carrying the real SB4 device configuration."""
    full_config, data_points, _ = _load_sb4()
    coordinator = FakeCoordinator()
    coordinator._full_config_cache = full_config
    coordinator._device_config_cache = data_points
    coordinator.async_update_listeners = MagicMock()
    return coordinator


@pytest.fixture
def controller(coord) -> ExportLimitController:
    result = get_export_limit_controller(coord)
    assert result is not None
    return result


def _make_select(coord, controller) -> AnkerSolixExportLimitSelect:
    entity = AnkerSolixExportLimitSelect(coord, controller)
    attach_entity_hass(entity, f"select.{SELECT_KEY}")
    return entity


def _make_number(coord, controller) -> AnkerSolixExportLimitNumber:
    entity = AnkerSolixExportLimitNumber(coord, controller)
    attach_entity_hass(entity, f"number.{NUMBER_KEY}")
    return entity


def _writes(coord) -> list[tuple[int, int, str]]:
    return [call.args for call in coord.write_register.await_args_list]


# --- device configuration ---------------------------------------------------


@pytest.mark.parametrize("key", sorted(RAW_DATA_POINTS))
class TestRawDataPoints:
    def test_matches_protocol_sheet(self, key: str) -> None:
        _, data_points, _ = _load_sb4()
        dp = data_points[key]
        assert (dp["address"], dp["data_type"], dp["count"]) == RAW_DATA_POINTS[key]

    def test_is_internal_only(self, key: str) -> None:
        """The raw registers are not entities of their own."""
        _, data_points, _ = _load_sb4()
        dp = data_points[key]
        assert dp.get("internal") is True
        assert not _is_sensor_entity(key, dp)
        assert not number_platform._is_number_entity(key, dp)
        assert not select_platform._is_select_entity(key, dp)

    def test_sits_inside_a_holding_batch_range(self, key: str) -> None:
        _, data_points, batch_ranges = _load_sb4()
        dp = data_points[key]
        last = dp["address"] + dp["count"] - 1
        assert any(
            start <= dp["address"] and last <= end and reg_type == "holding"
            for start, end, reg_type in batch_ranges
        )


def test_sb4_section_matches_the_app_behaviour() -> None:
    full_config, data_points, _ = _load_sb4()
    config = get_export_limit_config(full_config, data_points)
    assert config is not None
    assert (config.enable_key, config.enable_address, config.enable_data_type) == (
        ENABLE,
        10074,
        "UINT16",
    )
    assert (config.value_key, config.value_address, config.value_data_type) == (
        VALUE,
        10075,
        "INT32",
    )
    assert config.unlimited_value == UNLIMITED
    assert (config.min_value, config.max_value, config.step) == (100, 10000, 1)


def test_entity_keys_do_not_collide_with_data_points() -> None:
    """Same key would mean the same unique_id as a Modbus entity."""
    _, data_points, _ = _load_sb4()
    assert SELECT_KEY not in data_points
    assert NUMBER_KEY not in data_points


MINIMAL_DATA_POINTS = {
    "en": {"address": 10074, "data_type": "UINT16"},
    "val": {"address": 10075, "data_type": "INT32"},
}
MINIMAL_SECTION = {
    "enable_entity": "en",
    "value_entity": "val",
    "unlimited_value": -1,
    "min_value": 100,
    "max_value": 10000,
}


class TestConfigValidation:
    DATA_POINTS = MINIMAL_DATA_POINTS
    SECTION = MINIMAL_SECTION

    def test_unlimited_marker_may_lie_above_the_range(self) -> None:
        section = {**self.SECTION, "unlimited_value": 65535}
        config = get_export_limit_config({"export_limit": section}, self.DATA_POINTS)
        assert config is not None
        assert config.unlimited_value == 65535

    def test_valid_section_with_default_step(self) -> None:
        config = get_export_limit_config(
            {"export_limit": self.SECTION}, self.DATA_POINTS
        )
        assert config is not None
        assert config.step == 1

    @pytest.mark.parametrize("full_config", [None, [], {}, {"export_limit": None}])
    def test_absent_section(self, full_config) -> None:
        assert get_export_limit_config(full_config, self.DATA_POINTS) is None

    @pytest.mark.parametrize(
        "override",
        [
            {"enable_entity": "missing"},
            {"value_entity": None},
            {"unlimited_value": None},
            {"max_value": None},
            {"min_value": 20000},
            {"unlimited_value": 0},
            {"unlimited_value": 400},
            {"unlimited_value": 10000},
            {"min_value": -1},
            {"step": 0},
        ],
    )
    def test_invalid_section_is_ignored(self, override) -> None:
        section = {**self.SECTION, **override}
        assert (
            get_export_limit_config({"export_limit": section}, self.DATA_POINTS) is None
        )

    def test_section_must_be_a_mapping(self) -> None:
        assert (
            get_export_limit_config({"export_limit": "x"}, self.DATA_POINTS) is None
        )
        assert get_export_limit_config({"export_limit": self.SECTION}, None) is None

    def test_data_point_without_address_is_ignored(self) -> None:
        data_points = {**self.DATA_POINTS, "en": {"data_type": "UINT16"}}
        assert (
            get_export_limit_config({"export_limit": self.SECTION}, data_points) is None
        )


# --- mode resolution --------------------------------------------------------


class TestResolveMode:
    @pytest.mark.parametrize(
        ("enable", "value", "expected"),
        [
            # The three combinations the Anker app writes.
            (1, -1, MODE_UNLIMITED),
            (1, 400, MODE_CUSTOM),
            (0, 0, MODE_ZERO_EXPORT),
            # Enable register decides; the value is irrelevant when it is 0.
            (0, 400, MODE_ZERO_EXPORT),
            (0, None, MODE_ZERO_EXPORT),
            (1, 0, MODE_CUSTOM),
            # 0x0000FFFF is a (huge) limit, not the "unlimited" marker.
            (1, 65535, MODE_CUSTOM),
            ("1", "400", MODE_CUSTOM),
        ],
    )
    def test_known_combinations(self, enable, value, expected) -> None:
        assert resolve_mode(enable, value, UNLIMITED) == expected

    @pytest.mark.parametrize(
        ("enable", "value"),
        [(None, 400), (1, None), ("x", 400), (1, "x"), (True, 400), (1, -2)],
    )
    def test_unreadable_values_give_no_mode(self, enable, value) -> None:
        assert resolve_mode(enable, value, UNLIMITED) is None


# --- controller -------------------------------------------------------------


class TestControllerState:
    def test_mode_from_device_data(self, coord, controller) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        assert controller.mode == MODE_CUSTOM

    def test_mode_none_without_data(self, coord, controller) -> None:
        coord.data = None
        assert controller.mode is None

    def test_protected_values_win_over_device_data(self, coord, controller) -> None:
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        coord._protected[ENABLE] = 0
        coord._protected[VALUE] = 0
        assert controller.mode == MODE_ZERO_EXPORT

    def test_power_is_device_value_in_custom_mode(self, coord, controller) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        assert controller.power == 400

    def test_power_defaults_to_min_value(self, coord, controller) -> None:
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        assert controller.power == 100

    def test_power_remembers_last_custom_value(self, coord, controller) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        assert controller.power == 400
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        assert controller.power == 400
        coord.data = {ENABLE: 0, VALUE: 0}
        assert controller.power == 400

    def test_out_of_range_device_limit_is_shown_but_not_remembered(
        self, coord, controller
    ) -> None:
        """A limit outside 100..10000 W is not reused when "custom" is selected."""
        coord.data = {ENABLE: 1, VALUE: 65535}
        assert controller.mode == MODE_CUSTOM
        assert controller.power == 65535
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        assert controller.power == 100

    def test_remember_power_without_overwrite(self, controller) -> None:
        controller.remember_power(600, overwrite=False)
        controller.remember_power(700, overwrite=False)
        assert controller.power == 600
        controller.remember_power(700)
        assert controller.power == 700


class TestControllerApply:
    async def test_unlimited_writes_limit_then_enable(self, coord, controller) -> None:
        coord.data = {ENABLE: 0, VALUE: 0}
        assert await controller.async_apply(MODE_UNLIMITED)
        assert _writes(coord) == [(10075, UNLIMITED, "INT32"), (10074, 1, "UINT16")]
        assert controller.mode == MODE_UNLIMITED

    async def test_zero_export_writes_enable_then_limit(
        self, coord, controller
    ) -> None:
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        assert await controller.async_apply(MODE_ZERO_EXPORT)
        assert _writes(coord) == [(10074, 0, "UINT16"), (10075, 0, "INT32")]
        assert controller.mode == MODE_ZERO_EXPORT

    async def test_custom_writes_given_power(self, coord, controller) -> None:
        coord.data = {ENABLE: 0, VALUE: 0}
        assert await controller.async_apply(MODE_CUSTOM, 400)
        assert _writes(coord) == [(10075, 400, "INT32"), (10074, 1, "UINT16")]
        assert controller.mode == MODE_CUSTOM
        assert controller.power == 400

    async def test_custom_without_power_uses_remembered_value(
        self, coord, controller
    ) -> None:
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        controller.remember_power(800)
        assert await controller.async_apply(MODE_CUSTOM)
        assert _writes(coord)[0] == (10075, 800, "INT32")

    async def test_custom_without_any_known_power_uses_min_value(
        self, coord, controller
    ) -> None:
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        assert await controller.async_apply(MODE_CUSTOM)
        assert _writes(coord)[0] == (10075, 100, "INT32")

    async def test_success_protects_both_values_and_refreshes(
        self, coord, controller
    ) -> None:
        assert await controller.async_apply(MODE_CUSTOM, 400)
        assert coord._protected == {VALUE: 400, ENABLE: 1}
        coord.async_update_listeners.assert_called_once()

    async def test_first_write_failure_skips_second(self, coord, controller) -> None:
        coord.data = {ENABLE: 0, VALUE: 0}
        coord.write_register.return_value = make_write_result(
            success=False, error_reason="Illegal Data Value"
        )
        assert not await controller.async_apply(MODE_CUSTOM, 400)
        assert _writes(coord) == [(10075, 400, "INT32")]
        assert coord._protected == {}
        assert controller.mode == MODE_ZERO_EXPORT
        coord.async_update_listeners.assert_called_once()
        coord.device_logger.error.assert_called_once()

    async def test_transient_failure_logs_warning(self, coord, controller) -> None:
        coord.write_register.return_value = make_write_result(
            success=False, is_transient=True
        )
        assert not await controller.async_apply(MODE_UNLIMITED)
        coord.device_logger.warning.assert_called_once()
        coord.device_logger.error.assert_not_called()

    async def test_second_write_failure_keeps_first_value(
        self, coord, controller
    ) -> None:
        """Limit written, enable rejected: the display follows the device."""
        coord.data = {ENABLE: 0, VALUE: 0}
        coord.write_register.side_effect = [
            make_write_result(),
            make_write_result(success=False),
        ]
        assert not await controller.async_apply(MODE_CUSTOM, 400)
        assert coord._protected == {VALUE: 400}
        assert controller.mode == MODE_ZERO_EXPORT
        # A failed attempt does not change what "custom" will write next time.
        coord._protected.clear()
        assert controller.power == 100

    async def test_write_exception_is_reported_as_failure(
        self, coord, controller
    ) -> None:
        coord.write_register.side_effect = OSError("connection lost")
        assert not await controller.async_apply(MODE_ZERO_EXPORT)
        assert len(_writes(coord)) == 1
        coord.device_logger.error.assert_called_once()
        coord.async_update_listeners.assert_called_once()

    async def test_unknown_mode_writes_nothing(self, coord, controller) -> None:
        assert not await controller.async_apply("percentage")
        coord.write_register.assert_not_awaited()


# --- select entity ----------------------------------------------------------


class TestSelect:
    def test_identity_and_options(self, coord, controller) -> None:
        entity = _make_select(coord, controller)
        assert entity.unique_id == f"test-entry_{SELECT_KEY}"
        assert entity.translation_key == SELECT_KEY
        assert entity.options == list(MODES)
        assert entity.icon == "mdi:transmission-tower-export"

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({ENABLE: 1, VALUE: UNLIMITED}, MODE_UNLIMITED),
            ({ENABLE: 1, VALUE: 400}, MODE_CUSTOM),
            ({ENABLE: 0, VALUE: 0}, MODE_ZERO_EXPORT),
            ({}, None),
        ],
    )
    def test_current_option(self, coord, controller, data, expected) -> None:
        coord.data = data
        assert _make_select(coord, controller).current_option == expected

    def test_current_option_none_when_unavailable(self, coord, controller) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        coord.last_update_success = False
        assert _make_select(coord, controller).current_option is None

    @pytest.mark.parametrize(
        ("option", "expected_writes"),
        [
            (MODE_UNLIMITED, [(10075, UNLIMITED, "INT32"), (10074, 1, "UINT16")]),
            (MODE_CUSTOM, [(10075, 400, "INT32"), (10074, 1, "UINT16")]),
            (MODE_ZERO_EXPORT, [(10074, 0, "UINT16"), (10075, 0, "INT32")]),
        ],
    )
    async def test_select_option_writes_app_combination(
        self, coord, controller, option, expected_writes
    ) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        entity = _make_select(coord, controller)
        await entity.async_select_option(option)
        assert _writes(coord) == expected_writes
        assert entity.current_option == option
        coord.hass.services.async_call.assert_awaited_once()
        assert coord.hass.services.async_call.await_args.args[:2] == ("logbook", "log")

    async def test_custom_logbook_entry_names_the_limit(
        self, coord, controller
    ) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        entity = _make_select(coord, controller)
        await entity.async_select_option(MODE_CUSTOM)
        message = coord.hass.services.async_call.await_args.args[2]["message"]
        assert message.endswith("Custom 400 W")

    async def test_failed_write_raises(self, coord, controller) -> None:
        coord.write_register.return_value = make_write_result(success=False)
        entity = _make_select(coord, controller)
        with pytest.raises(HomeAssistantError) as err:
            await entity.async_select_option(MODE_UNLIMITED)
        assert err.value.translation_key == "export_limit_write_failed"
        coord.hass.services.async_call.assert_not_awaited()

    def test_state_attributes(self, coord, controller) -> None:
        attrs = _make_select(coord, controller).extra_state_attributes
        assert attrs == {"enable_address": 10074, "value_address": 10075}


# --- number entity ----------------------------------------------------------


class TestNumber:
    def test_identity_and_range(self, coord, controller) -> None:
        entity = _make_number(coord, controller)
        assert entity.unique_id == f"test-entry_{NUMBER_KEY}"
        assert entity.translation_key == NUMBER_KEY
        assert entity.native_min_value == 100
        assert entity.native_max_value == 10000
        assert entity.native_step == 1
        assert entity.native_unit_of_measurement == "W"

    def test_shows_device_value_in_custom_mode(self, coord, controller) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        assert _make_number(coord, controller).native_value == 400

    def test_shows_remembered_value_outside_custom_mode(
        self, coord, controller
    ) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        entity = _make_number(coord, controller)
        assert entity.native_value == 400
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        assert entity.native_value == 400

    def test_none_when_unavailable(self, coord, controller) -> None:
        coord.last_update_success = False
        assert _make_number(coord, controller).native_value is None

    async def test_set_value_writes_limit_and_activates_it(
        self, coord, controller
    ) -> None:
        coord.data = {ENABLE: 0, VALUE: 0}
        entity = _make_number(coord, controller)
        await entity.async_set_native_value(600.0)
        assert _writes(coord) == [(10075, 600, "INT32"), (10074, 1, "UINT16")]
        assert entity.native_value == 600
        assert controller.mode == MODE_CUSTOM
        message = coord.hass.services.async_call.await_args.args[2]["message"]
        assert message.endswith("600 W")

    async def test_failed_write_raises_and_keeps_value(
        self, coord, controller
    ) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        coord.write_register.return_value = make_write_result(success=False)
        entity = _make_number(coord, controller)
        with pytest.raises(HomeAssistantError):
            await entity.async_set_native_value(600.0)
        assert entity.native_value == 400

    async def test_restores_last_custom_limit(self, hass, coord, controller) -> None:
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        entity = _make_number(coord, controller)
        entity.hass = hass
        entity.async_get_last_number_data = AsyncMock(
            return_value=SimpleNamespace(native_value=750.0)
        )
        await entity.async_added_to_hass()
        assert entity.native_value == 750

    async def test_restore_does_not_override_device_value(
        self, hass, coord, controller
    ) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        entity = _make_number(coord, controller)
        assert entity.native_value == 400
        entity.hass = hass
        entity.async_get_last_number_data = AsyncMock(
            return_value=SimpleNamespace(native_value=750.0)
        )
        await entity.async_added_to_hass()
        assert entity.native_value == 400

    @pytest.mark.parametrize("last", [None, SimpleNamespace(native_value=None)])
    async def test_nothing_to_restore(self, hass, coord, controller, last) -> None:
        coord.data = {ENABLE: 1, VALUE: UNLIMITED}
        entity = _make_number(coord, controller)
        entity.hass = hass
        entity.async_get_last_number_data = AsyncMock(return_value=last)
        await entity.async_added_to_hass()
        assert entity.native_value == 100

    def test_state_attributes(self, coord, controller) -> None:
        coord.data = {ENABLE: 1, VALUE: 400}
        attrs = _make_number(coord, controller).extra_state_attributes
        assert attrs == {"modbus_address": 10075, "data_type": "INT32", "active": True}


# --- platform wiring --------------------------------------------------------


class TestFactories:
    def test_select_and_number_share_one_controller(self, coord) -> None:
        (select,) = create_export_limit_selects(coord)
        (number,) = create_export_limit_numbers(coord)
        assert select._controller is number._controller

    def test_device_without_section_gets_no_entities(self) -> None:
        coordinator = FakeCoordinator()
        coordinator._full_config_cache = {"read_quantities": {}}
        coordinator._device_config_cache = {}
        assert create_export_limit_selects(coordinator) == []
        assert create_export_limit_numbers(coordinator) == []

    def test_coordinator_without_config_gets_no_entities(self) -> None:
        assert create_export_limit_selects(FakeCoordinator()) == []
        assert create_export_limit_selects(MagicMock()) == []

    @pytest.mark.parametrize(
        ("platform", "entity_type"),
        [
            (select_platform, AnkerSolixExportLimitSelect),
            (number_platform, AnkerSolixExportLimitNumber),
        ],
    )
    async def test_platform_setup_adds_the_entity(
        self, coord, platform, entity_type
    ) -> None:
        coord.get_device_data_points = AsyncMock(
            return_value=coord._device_config_cache
        )
        coord.ip_address = "192.0.2.1"
        hass = SimpleNamespace(data={DOMAIN: {"test-entry": coord}})
        entry = SimpleNamespace(entry_id="test-entry")
        added: list[Any] = []
        await platform.async_setup_entry(hass, entry, added.extend)
        assert [type(e) for e in added].count(entity_type) == 1

    def test_only_the_sb4_config_declares_the_section(self) -> None:
        declaring = [
            path.name
            for path in sorted((COMPONENT_DIR / "config").glob("*.yaml"))
            if "export_limit" in yaml.safe_load(path.read_text(encoding="utf-8"))
        ]
        assert declaring == [SB4_CONFIG_PATH.name]


# --- translations -----------------------------------------------------------


def test_entity_translations_exist_in_all_languages() -> None:
    for lang_file in _lang_files():
        entity = json.loads(lang_file.read_text(encoding="utf-8"))["entity"]
        select = entity["select"].get(SELECT_KEY, {})
        assert select.get("name"), lang_file.name
        assert set(select.get("state", {})) == set(MODES), lang_file.name
        assert all(select["state"].values()), lang_file.name
        assert entity["number"].get(NUMBER_KEY, {}).get("name"), lang_file.name


def test_error_translation_exists_wherever_exceptions_are_translated() -> None:
    checked = 0
    for lang_file in _lang_files():
        exceptions = json.loads(lang_file.read_text(encoding="utf-8")).get("exceptions")
        if exceptions is None:
            # strings.json carries no exception texts (upstream layout).
            continue
        checked += 1
        assert exceptions.get("export_limit_write_failed", {}).get(
            "message"
        ), lang_file.name
    assert checked >= 4


def test_german_names_match_the_anker_app() -> None:
    de = json.loads(
        (COMPONENT_DIR / "translations" / "de.json").read_text(encoding="utf-8")
    )["entity"]
    assert de["select"][SELECT_KEY] == {
        "name": "Exportbegrenzung",
        "state": {
            "unlimited": "Unbegrenzt",
            "custom": "Anpassen",
            "zero_export": "Null-Export",
        },
    }
