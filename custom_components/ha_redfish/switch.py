"""Switch platform for HA Redfish power and Chassis UID LED control."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CAT_SYSTEM,
    CAT_THERMAL_POWER,
    DOMAIN,
    RESET_FORCE_OFF,
    RESET_GRACEFUL_SHUTDOWN,
    RESET_ON,
)
from .coordinator import RedfishDataUpdateCoordinator
from .entity import RedfishEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Redfish switches."""
    coordinator: RedfishDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [
        RedfishPowerSwitch(coordinator),
        RedfishIndicatorLedSwitch(coordinator),
    ]
    async_add_entities(entities)


class RedfishPowerSwitch(RedfishEntity, SwitchEntity):
    """Switch representing main server power (On vs GracefulShutdown)."""

    _attr_name = "Power"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:server"

    def __init__(self, coordinator: RedfishDataUpdateCoordinator) -> None:
        """Initialize the power switch."""
        super().__init__(coordinator, "server_power")

    @property
    def is_on(self) -> bool | None:
        """Return True if PowerState is On."""
        power_state = self.coordinator.data.get(CAT_SYSTEM, {}).get("PowerState")
        if power_state is None:
            return None
        return power_state == "On"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Power on the server and schedule a thermal/power rediscovery sweep."""
        await self.coordinator.client.async_reset_system(RESET_ON)
        await self.coordinator.async_force_category_refresh(CAT_SYSTEM)
        await self.coordinator.async_force_category_refresh(CAT_THERMAL_POWER)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Gracefully shut down the server (falls back to ForceOff if unsupported)."""
        allowable = self.coordinator.client.allowable_reset_types
        reset_cmd = (
            RESET_GRACEFUL_SHUTDOWN
            if RESET_GRACEFUL_SHUTDOWN in allowable
            else RESET_FORCE_OFF
        )
        await self.coordinator.client.async_reset_system(reset_cmd)
        await self.coordinator.async_force_category_refresh(CAT_SYSTEM)


class RedfishIndicatorLedSwitch(RedfishEntity, SwitchEntity):
    """Switch to control the physical Chassis UID / Indicator LED."""

    _attr_name = "UID Indicator LED"
    _attr_icon = "mdi:led-on"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: RedfishDataUpdateCoordinator) -> None:
        """Initialize the UID LED switch."""
        super().__init__(coordinator, "uid_led")

    @property
    def is_on(self) -> bool | None:
        """Return True if IndicatorLED is Lit or Blinking."""
        system = self.coordinator.data.get(CAT_SYSTEM, {})
        if "LocationIndicatorActive" in system:
            return bool(system["LocationIndicatorActive"])
        led = system.get("IndicatorLED")
        if led is None:
            return None
        return led in ("Lit", "Blinking")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the Chassis UID LED."""
        system = self.coordinator.data.get(CAT_SYSTEM, {})
        payload = (
            {"LocationIndicatorActive": True}
            if "LocationIndicatorActive" in system
            else {"IndicatorLED": "Lit"}
        )
        if self.coordinator.client.system_uri:
            await self.coordinator.client.async_request(
                "PATCH", self.coordinator.client.system_uri, json_data=payload
            )
            await self.coordinator.async_force_category_refresh(CAT_SYSTEM)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the Chassis UID LED."""
        system = self.coordinator.data.get(CAT_SYSTEM, {})
        payload = (
            {"LocationIndicatorActive": False}
            if "LocationIndicatorActive" in system
            else {"IndicatorLED": "Off"}
        )
        if self.coordinator.client.system_uri:
            await self.coordinator.client.async_request(
                "PATCH", self.coordinator.client.system_uri, json_data=payload
            )
            await self.coordinator.async_force_category_refresh(CAT_SYSTEM)