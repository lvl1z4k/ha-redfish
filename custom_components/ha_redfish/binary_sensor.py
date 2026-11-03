"""Binary sensor platform for HA Redfish."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAT_SYSTEM, CAT_THERMAL_POWER, DOMAIN
from .coordinator import RedfishDataUpdateCoordinator
from .entity import RedfishEntity
from .sensor import ACTIVE_PSU_STATES, _get_member_key, _is_populated


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Redfish binary sensors with dynamic PSU and OEM fault detection."""
    coordinator: RedfishDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_ids: set[str] = set()

    # 1. Static system-level problem sensor
    async_add_entities([RedfishSystemProblemBinarySensor(coordinator)])

    # 2. Dynamic callback for PSU fault sensors and Vendor OEM binary sensors
    @callback
    def _async_discover_dynamic_binary_sensors() -> None:
        new_entities: list[BinarySensorEntity] = []

        # Discover Individual PSU Fault Binary Sensors
        psus = (
            coordinator.data.get(CAT_THERMAL_POWER, {})
            .get("power", {})
            .get("PowerSupplies", [])
        )
        for idx, psu in enumerate(psus):
            if not _is_populated(psu, ACTIVE_PSU_STATES):
                continue
            member_key = _get_member_key(psu, idx)
            uid = f"psu_{member_key}_fault"
            if uid not in known_ids:
                known_ids.add(uid)
                psu_name = psu.get("Name") or f"PSU {idx + 1}"
                new_entities.append(
                    RedfishPsuFaultBinarySensor(
                        coordinator, uid, member_key, f"{psu_name} Power Feed"
                    )
                )

        # Discover Vendor OEM Binary Sensors (HPE iLO / Dell iDRAC Rollups & Redundancy)
        oem_bin_sensors = coordinator.data.get(CAT_OEM, {}).get("binary_sensors", {})
        for oem_key, metric in oem_bin_sensors.items():
            uid = f"oem_bin_{oem_key}"
            if uid not in known_ids and metric.is_on is not None:
                known_ids.add(uid)
                new_entities.append(
                    RedfishOemBinarySensor(coordinator, uid, oem_key)
                )

        if new_entities:
            async_add_entities(new_entities)

    # Run initial discovery and attach listener for future coordinator updates
    _async_discover_dynamic_binary_sensors()
    entry.async_on_unload(
        coordinator.async_add_listener(_async_discover_dynamic_binary_sensors)
    )


class RedfishSystemProblemBinarySensor(RedfishEntity, BinarySensorEntity):
    """Binary sensor indicating overall server health warning or critical state."""

    _attr_name = "System Problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: RedfishDataUpdateCoordinator) -> None:
        """Initialize the system problem sensor."""
        super().__init__(coordinator, "system_problem")

    @property
    def is_on(self) -> bool | None:
        """Return True if HealthRollup or Health is not OK."""
        status = self.coordinator.data.get(CAT_SYSTEM, {}).get("Status", {})
        health = status.get("HealthRollup") or status.get("Health")
        if health is None:
            return None
        return health.upper() != "OK"


class RedfishPsuFaultBinarySensor(RedfishEntity, BinarySensorEntity):
    """Binary sensor that monitors PSU input power health (On = Power OK, Off = Fault/0V)."""

    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        uid_suffix: str,
        member_key: str,
        name: str,
    ) -> None:
        """Initialize the PSU fault binary sensor."""
        super().__init__(coordinator, uid_suffix)
        self._member_key = member_key
        self._attr_name = name

    def _find_psu(self) -> dict[str, Any] | None:
        psus = (
            self.coordinator.data.get(CAT_THERMAL_POWER, {})
            .get("power", {})
            .get("PowerSupplies", [])
        )
        for idx, psu in enumerate(psus):
            if _get_member_key(psu, idx) == self._member_key:
                return psu
        return None

    @property
    def available(self) -> bool:
        """Return True if PSU is populated."""
        psu = self._find_psu()
        return super().available and psu is not None and _is_populated(psu, ACTIVE_PSU_STATES)

    @property
    def is_on(self) -> bool:
        """Return True if PSU has a healthy input feed, False on fault or 0V input."""
        psu = self._find_psu()
        if not psu:
            return False
        health = (psu.get("Status", {}).get("Health") or "OK").upper()
        line_voltage = psu.get("LineInputVoltage")
        if health != "OK":
            return False
        if line_voltage is not None and line_voltage <= 0:
            return False
        return True


class RedfishOemBinarySensor(RedfishEntity, BinarySensorEntity):
    """Binary sensor representing an OEM subsystem health rollup or redundancy fault."""

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        uid_suffix: str,
        oem_key: str,
    ) -> None:
        """Initialize the OEM binary sensor."""
        super().__init__(coordinator, uid_suffix)
        self._oem_key = oem_key
        metric = self._get_metric()
        if metric:
            self._attr_name = metric.name
            self._attr_device_class = metric.device_class
            self._attr_icon = metric.icon
            self._attr_entity_category = metric.entity_category

    def _get_metric(self) -> Any:
        return (
            self.coordinator.data.get(CAT_OEM, {})
            .get("binary_sensors", {})
            .get(self._oem_key)
        )

    @property
    def available(self) -> bool:
        """Return True if the OEM binary metric is present."""
        return super().available and self._get_metric() is not None

    @property
    def is_on(self) -> bool | None:
        """Return True if a problem/fault is detected."""
        metric = self._get_metric()
        return metric.is_on if metric else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return vendor-specific status details."""
        metric = self._get_metric()
        if not metric:
            return {}
        return {"vendor_extension": metric.vendor, **metric.attributes}