"""Base entity for the HA Redfish integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CAT_INVENTORY, CAT_SYSTEM, DOMAIN
from .coordinator import RedfishDataUpdateCoordinator


class RedfishEntity(CoordinatorEntity[RedfishDataUpdateCoordinator]):
    """Base class for all HA Redfish entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        unique_id_suffix: str,
    ) -> None:
        """Initialize the Redfish entity."""
        super().__init__(coordinator)

        system = coordinator.data.get(CAT_SYSTEM, {})
        manager = coordinator.data.get(CAT_INVENTORY, {}).get("manager", {})

        raw_uuid = (system.get("UUID") or "").strip()
        valid_uuid = (
            raw_uuid
            if raw_uuid and raw_uuid.replace("-", "").strip("0")
            else None
        )

        self._base_unique_id = (
            valid_uuid
            or (system.get("SerialNumber") or "").strip()
            or coordinator.entry.entry_id
        )
        self._attr_unique_id = f"{self._base_unique_id}_{unique_id_suffix}"

        manufacturer = system.get("Manufacturer", "Generic Redfish")
        model = system.get("Model", "Server")
        hostname = system.get("HostName") or coordinator.client.host

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._base_unique_id)},
            name=f"{model} ({hostname})",
            manufacturer=manufacturer,
            model=model,
            serial_number=system.get("SerialNumber"),
            hw_version=system.get("SKU"),
            sw_version=manager.get("FirmwareVersion") or system.get("BiosVersion"),
            configuration_url=f"https://{coordinator.client.host}:{coordinator.entry.data['port']}",
        )