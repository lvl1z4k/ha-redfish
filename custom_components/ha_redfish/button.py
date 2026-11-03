"""Button platform for HA Redfish power actions and hardware rediscovery."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CAT_INVENTORY,
    CAT_SYSTEM,
    CAT_THERMAL_POWER,
    DOMAIN,
    RESET_FORCE_OFF,
    RESET_FORCE_RESTART,
    RESET_GRACEFUL_RESTART,
    RESET_NMI,
    RESET_POWER_CYCLE,
)
from .coordinator import RedfishDataUpdateCoordinator
from .entity import RedfishEntity


@dataclass(frozen=True, kw_only=True)
class RedfishResetButtonDescription(ButtonEntityDescription):
    """Describes a Redfish ComputerSystem.Reset button."""

    reset_type: str


RESET_BUTTONS: tuple[RedfishResetButtonDescription, ...] = (
    RedfishResetButtonDescription(
        key="graceful_restart",
        name="Graceful Restart",
        device_class=ButtonDeviceClass.RESTART,
        reset_type=RESET_GRACEFUL_RESTART,
    ),
    RedfishResetButtonDescription(
        key="force_restart",
        name="Force Restart (Cold Boot)",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        reset_type=RESET_FORCE_RESTART,
    ),
    RedfishResetButtonDescription(
        key="force_off",
        name="Force Power Off",
        icon="mdi:power-off",
        entity_category=EntityCategory.CONFIG,
        reset_type=RESET_FORCE_OFF,
    ),
    RedfishResetButtonDescription(
        key="power_cycle",
        name="Power Cycle",
        icon="mdi:power-cycle",
        entity_category=EntityCategory.CONFIG,
        reset_type=RESET_POWER_CYCLE,
    ),
    RedfishResetButtonDescription(
        key="nmi",
        name="Trigger NMI",
        icon="mdi:alert-octagon-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        reset_type=RESET_NMI,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Redfish action buttons and the hardware rediscovery button."""
    coordinator: RedfishDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    allowable = coordinator.client.allowable_reset_types

    entities: list[ButtonEntity] = [
        RedfishRediscoverButton(coordinator),
    ]

    for desc in RESET_BUTTONS:
        if not allowable or desc.reset_type in allowable:
            entities.append(RedfishResetButton(coordinator, desc))

    async_add_entities(entities)


class RedfishResetButton(RedfishEntity, ButtonEntity):
    """Button to execute a DMTF ComputerSystem.Reset action."""

    entity_description: RedfishResetButtonDescription

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        description: RedfishResetButtonDescription,
    ) -> None:
        """Initialize the reset button."""
        super().__init__(coordinator, f"reset_{description.key}")
        self.entity_description = description

    async def async_press(self) -> None:
        """Send the reset command and immediately refresh system state."""
        await self.coordinator.client.async_reset_system(
            self.entity_description.reset_type
        )
        await self.coordinator.async_force_category_refresh(CAT_SYSTEM)


class RedfishRediscoverButton(RedfishEntity, ButtonEntity):
    """Button to force endpoint re-evaluation and discover newly enabled sensors."""

    _attr_name = "Rediscover Hardware & Sensors"
    _attr_icon = "mdi:refresh-auto"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RedfishDataUpdateCoordinator) -> None:
        """Initialize the rediscover button."""
        super().__init__(coordinator, "rediscover_hardware")

    async def async_press(self) -> None:
        """Re-run endpoint discovery and force all telemetry collectors to refresh."""
        await self.coordinator.client.async_discover_endpoints()
        for category in (CAT_SYSTEM, CAT_THERMAL_POWER, CAT_INVENTORY):
            self.coordinator._last_updated.pop(category, None)
        await self.coordinator.async_request_refresh()