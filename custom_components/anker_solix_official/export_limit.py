"""Export limitation controls (fork feature).

A device YAML may declare an ``export_limit`` section. It turns two raw data
points into the three states the Anker app offers for "export limitation":

    export_limit:
      enable_entity: "export_limit_enable"   # data point key, 0/1
      value_entity: "export_limit_value"     # data point key, limit in W
      unlimited_value: 65535                 # limit value meaning "no limit"
      min_value: 100                         # range of the custom limit in W
      max_value: 10000
      step: 1

Register combinations, as written by the app (verified on a Solarbank 4 E5000
Pro; the enable register is effectively "export allowed", not "limit active"):

    mode         enable  value
    unlimited    1       unlimited_value
    custom       1       limit in W
    zero_export  0       0

Two entities are created: a select with the three modes and a number with the
custom limit. Every change writes both registers, ordered so that the state in
between is never more permissive than both the old and the new state.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberMode,
    RestoreNumber,
)
from homeassistant.components.select import SelectEntity
from homeassistant.const import UnitOfPower
from homeassistant.exceptions import HomeAssistantError

from .base_entity import AnkerSolixBaseEntity
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import AnkerSolixOfficialCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SECTION = "export_limit"

MODE_UNLIMITED = "unlimited"
MODE_CUSTOM = "custom"
MODE_ZERO_EXPORT = "zero_export"
# Same order as in the Anker app.
MODES = (MODE_UNLIMITED, MODE_CUSTOM, MODE_ZERO_EXPORT)

SELECT_KEY = "export_limit_mode"
NUMBER_KEY = "export_limit_power"

WRITE_PROTECTION_SECONDS = 10.0
_CONTROLLER_ATTR = "_export_limit_controller"


@dataclass(frozen=True)
class ExportLimitConfig:
    """Validated ``export_limit`` section of a device configuration."""

    enable_key: str
    enable_address: int
    enable_data_type: str
    value_key: str
    value_address: int
    value_data_type: str
    unlimited_value: int
    min_value: int
    max_value: int
    step: int


def _to_int(value: Any) -> int | None:
    """Return value as int, or None if it is not a whole number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def get_export_limit_config(
    full_config: Any, data_points: Any
) -> ExportLimitConfig | None:
    """Return the validated ``export_limit`` section, or None if absent/unusable."""
    if not isinstance(full_config, dict):
        return None
    section = full_config.get(CONFIG_SECTION)
    if section is None:
        return None
    if not isinstance(section, dict) or not isinstance(data_points, dict):
        _LOGGER.warning("Ignoring '%s': section is not usable", CONFIG_SECTION)
        return None

    registers: dict[str, tuple[str, int, str]] = {}
    for option in ("enable_entity", "value_entity"):
        key = section.get(option)
        data_point = data_points.get(key) if isinstance(key, str) else None
        address = (
            _to_int(data_point.get("address")) if isinstance(data_point, dict) else None
        )
        if address is None:
            _LOGGER.warning(
                "Ignoring '%s': %s '%s' is not a data point with an address",
                CONFIG_SECTION,
                option,
                key,
            )
            return None
        registers[option] = (key, address, str(data_point.get("data_type", "UINT16")))

    unlimited_value = _to_int(section.get("unlimited_value"))
    min_value = _to_int(section.get("min_value", 0))
    max_value = _to_int(section.get("max_value"))
    step = _to_int(section.get("step", 1))
    if (
        unlimited_value is None
        or min_value is None
        or max_value is None
        or step is None
        or step < 1
        or not 0 <= min_value <= max_value < unlimited_value
    ):
        _LOGGER.warning(
            "Ignoring '%s': need 0 <= min_value <= max_value < unlimited_value "
            "and step >= 1",
            CONFIG_SECTION,
        )
        return None

    enable_key, enable_address, enable_data_type = registers["enable_entity"]
    value_key, value_address, value_data_type = registers["value_entity"]
    return ExportLimitConfig(
        enable_key=enable_key,
        enable_address=enable_address,
        enable_data_type=enable_data_type,
        value_key=value_key,
        value_address=value_address,
        value_data_type=value_data_type,
        unlimited_value=unlimited_value,
        min_value=min_value,
        max_value=max_value,
        step=step,
    )


def resolve_mode(enable: Any, value: Any, unlimited_value: int) -> str | None:
    """Map the two register values to a mode, or None if they cannot be read."""
    enable_int = _to_int(enable)
    if enable_int is None:
        return None
    if enable_int == 0:
        return MODE_ZERO_EXPORT
    value_int = _to_int(value)
    if value_int is None or value_int < 0:
        return None
    if value_int >= unlimited_value:
        return MODE_UNLIMITED
    return MODE_CUSTOM


class ExportLimitController:
    """State and write logic shared by the select and the number entity."""

    def __init__(
        self, coordinator: AnkerSolixOfficialCoordinator, config: ExportLimitConfig
    ) -> None:
        """Initialize the controller."""
        self.coordinator = coordinator
        self.config = config
        self._remembered_power: int | None = None
        self._write_lock = asyncio.Lock()

    def _raw(self, key: str) -> Any:
        """Return a data point value, preferring a value we just wrote."""
        is_protected, protected_value = self.coordinator.get_protected_value(key)
        if is_protected:
            return protected_value
        data = self.coordinator.data
        return data.get(key) if data else None

    @property
    def mode(self) -> str | None:
        """Return the current mode as reported by the device."""
        return resolve_mode(
            self._raw(self.config.enable_key),
            self._raw(self.config.value_key),
            self.config.unlimited_value,
        )

    @property
    def power(self) -> int:
        """Return the custom limit in W.

        In custom mode this is the limit the device reports. In the other modes
        the value register holds no limit, so the last known custom limit is
        returned: it is what selecting "custom" will write.
        """
        if self.mode == MODE_CUSTOM:
            device_value = _to_int(self._raw(self.config.value_key))
            if device_value is not None:
                self._remembered_power = device_value
        if self._remembered_power is None:
            return self.config.min_value
        return self._remembered_power

    def remember_power(self, power: int, overwrite: bool = True) -> None:
        """Store the custom limit to use when "custom" is selected."""
        if overwrite or self._remembered_power is None:
            self._remembered_power = power

    def _writes_for(self, mode: str, power: int) -> list[tuple[str, int, str, int]]:
        """Return (key, address, data_type, value) writes for a mode, in order."""
        cfg = self.config
        if mode == MODE_ZERO_EXPORT:
            # Close first: block the export, then clear the limit.
            return [
                (cfg.enable_key, cfg.enable_address, cfg.enable_data_type, 0),
                (cfg.value_key, cfg.value_address, cfg.value_data_type, 0),
            ]
        limit = cfg.unlimited_value if mode == MODE_UNLIMITED else power
        # Limit first: coming from zero export, the export is only released once
        # the new limit is in place.
        return [
            (cfg.value_key, cfg.value_address, cfg.value_data_type, limit),
            (cfg.enable_key, cfg.enable_address, cfg.enable_data_type, 1),
        ]

    async def async_apply(self, mode: str, power: int | None = None) -> bool:
        """Write a mode to the device. Returns True if both registers were written.

        Args:
            mode: One of MODES.
            power: Custom limit in W; defaults to the last known custom limit.
        """
        if mode not in MODES:
            _LOGGER.error("Unknown export limit mode '%s'", mode)
            return False
        if power is None:
            power = self.power

        dlog = self.coordinator.device_logger
        async with self._write_lock:
            try:
                for key, address, data_type, value in self._writes_for(mode, power):
                    dlog.debug(
                        "Writing export limit %s | address=%d (0x%04X), value=%d, "
                        "data_type=%s, mode='%s'",
                        key,
                        address,
                        address,
                        value,
                        data_type,
                        mode,
                    )
                    try:
                        result = await self.coordinator.modbus_manager.write_register(
                            address, value, data_type
                        )
                    except Exception as exc:
                        dlog.error(
                            "Write export limit EXCEPTION | %s: mode='%s', value=%d, "
                            "address=%d (0x%04X), error=%s",
                            key,
                            mode,
                            value,
                            address,
                            address,
                            exc,
                        )
                        return False
                    if not result.success:
                        log_fn = dlog.warning if result.is_transient else dlog.error
                        log_fn(
                            "Write export limit FAILED | entity=%s, mode='%s', "
                            "value=%d, address=%d (0x%04X), is_transient=%s, "
                            "reason=%s, raw_response=%s, tx_frame=%s",
                            key,
                            mode,
                            value,
                            address,
                            address,
                            result.is_transient,
                            result.error_reason,
                            result.raw_response or "N/A",
                            result.tx_frame or "N/A",
                        )
                        return False
                    self.coordinator.set_write_protection(
                        key, value, WRITE_PROTECTION_SECONDS
                    )
                if mode == MODE_CUSTOM:
                    self._remembered_power = power
                return True
            finally:
                # Both entities show the same registers: refresh them together,
                # also after a partial write.
                self.coordinator.async_update_listeners()


async def _async_log_change(entity: AnkerSolixBaseEntity, text: str) -> None:
    """Write a control action to the logbook, like the other control entities."""
    coordinator = entity.coordinator
    device_name = coordinator.device_name or "Anker Solix"
    entity_name = entity.name or entity._entity_key
    await coordinator.hass.services.async_call(
        "logbook",
        "log",
        {
            "name": device_name,
            "message": f"{entity_name} → {text}",
            "entity_id": entity.entity_id,
            "domain": DOMAIN,
        },
        blocking=False,
    )
    coordinator.device_logger.info("📝 %s %s → %s", device_name, entity_name, text)


def _write_failed() -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key="export_limit_write_failed"
    )


class AnkerSolixExportLimitSelect(AnkerSolixBaseEntity, SelectEntity):
    """Select with the export limitation modes of the Anker app."""

    def __init__(
        self,
        coordinator: AnkerSolixOfficialCoordinator,
        controller: ExportLimitController,
    ) -> None:
        """Initialize the select."""
        super().__init__(
            coordinator,
            SELECT_KEY,
            {"translation_key": SELECT_KEY, "icon": "mdi:transmission-tower-export"},
        )
        self._controller = controller
        self._attr_options = list(MODES)

    @property
    def current_option(self) -> str | None:
        """Return the mode reported by the device."""
        if not self.available:
            return None
        return self._controller.mode

    async def async_select_option(self, option: str) -> None:
        """Write the selected mode to the device."""
        if not await self._controller.async_apply(option):
            raise _write_failed()
        text = option.replace("_", " ").title()
        if option == MODE_CUSTOM:
            text = f"{text} {self._controller.power} W"
        await _async_log_change(self, text)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        cfg = self._controller.config
        return {
            "enable_address": cfg.enable_address,
            "value_address": cfg.value_address,
        }


class AnkerSolixExportLimitNumber(AnkerSolixBaseEntity, RestoreNumber):
    """Number with the custom export limit in W."""

    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        coordinator: AnkerSolixOfficialCoordinator,
        controller: ExportLimitController,
    ) -> None:
        """Initialize the number."""
        super().__init__(
            coordinator,
            NUMBER_KEY,
            {"translation_key": NUMBER_KEY, "icon": "mdi:transmission-tower-export"},
        )
        self._controller = controller
        self._attr_native_min_value = controller.config.min_value
        self._attr_native_max_value = controller.config.max_value
        self._attr_native_step = controller.config.step

    async def async_added_to_hass(self) -> None:
        """Restore the last custom limit (not readable outside custom mode)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        restored = _to_int(last.native_value) if last else None
        if restored is not None:
            self._controller.remember_power(restored, overwrite=False)

    @property
    def native_value(self) -> int | None:
        """Return the custom limit in W."""
        if not self.available:
            return None
        return self._controller.power

    async def async_set_native_value(self, value: float) -> None:
        """Write the limit and activate it (mode "custom")."""
        power = int(value)
        if not await self._controller.async_apply(MODE_CUSTOM, power):
            raise _write_failed()
        await _async_log_change(self, f"{power} W")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        cfg = self._controller.config
        return {
            "modbus_address": cfg.value_address,
            "data_type": cfg.value_data_type,
            "active": self._controller.mode == MODE_CUSTOM,
        }


def get_export_limit_controller(
    coordinator: AnkerSolixOfficialCoordinator,
) -> ExportLimitController | None:
    """Return the controller of a coordinator, or None if the device has none."""
    controller = getattr(coordinator, _CONTROLLER_ATTR, None)
    if isinstance(controller, ExportLimitController):
        return controller

    config = get_export_limit_config(
        getattr(coordinator, "_full_config_cache", None),
        getattr(coordinator, "_device_config_cache", None),
    )
    if config is None:
        return None

    controller = ExportLimitController(coordinator, config)
    setattr(coordinator, _CONTROLLER_ATTR, controller)
    return controller


def create_export_limit_selects(
    coordinator: AnkerSolixOfficialCoordinator,
) -> list[AnkerSolixExportLimitSelect]:
    """Create the export limitation select declared in the device configuration."""
    controller = get_export_limit_controller(coordinator)
    if controller is None:
        return []
    return [AnkerSolixExportLimitSelect(coordinator, controller)]


def create_export_limit_numbers(
    coordinator: AnkerSolixOfficialCoordinator,
) -> list[AnkerSolixExportLimitNumber]:
    """Create the export limit number declared in the device configuration."""
    controller = get_export_limit_controller(coordinator)
    if controller is None:
        return []
    return [AnkerSolixExportLimitNumber(coordinator, controller)]
