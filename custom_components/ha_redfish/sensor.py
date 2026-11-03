"""Sensor platform for HA Redfish with dynamic hardware discovery."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAT_INVENTORY, CAT_SYSTEM, CAT_THERMAL_POWER, DOMAIN, CAT_OEM
from .coordinator import RedfishDataUpdateCoordinator
from .entity import RedfishEntity

_LOGGER = logging.getLogger(__name__)

# Valid states for general sensors vs redundant PSUs
ACTIVE_SENSOR_STATES = {"Enabled"}
ACTIVE_PSU_STATES = {"Enabled", "StandbyOffline", "StandbySpare", "Quiesced"}


def _get_member_key(item: dict[str, Any], index: int) -> str:
    """Extract a stable unique identifier for an array member."""
    odata_id = item.get("@odata.id", "")
    if odata_id:
        return odata_id.rstrip("/").split("/")[-1].replace(".", "_").lower()
    member_id = item.get("MemberId")
    if member_id is not None:
        return str(member_id).lower()
    return str(index)


def _is_populated(item: dict[str, Any], allowed_states: set[str]) -> bool:
    """Return True if the Redfish resource is populated and active."""
    state = item.get("Status", {}).get("State")
    return state in allowed_states


@dataclass(frozen=True, kw_only=True)
class RedfishStaticSensorDescription(SensorEntityDescription):
    """Describes a static Redfish sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


STATIC_SENSORS: tuple[RedfishStaticSensorDescription, ...] = (
    RedfishStaticSensorDescription(
        key="total_power_watts",
        name="Total Power Consumption",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: (
            data.get(CAT_THERMAL_POWER, {})
            .get("power", {})
            .get("PowerControl", [{}])[0]
            .get("PowerConsumedWatts")
            if data.get(CAT_THERMAL_POWER, {}).get("power", {}).get("PowerControl")
            else None
        ),
    ),
    RedfishStaticSensorDescription(
        key="power_average_watts",
        name="Average Power Consumption",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: (
            data.get(CAT_THERMAL_POWER, {})
            .get("power", {})
            .get("PowerControl", [{}])[0]
            .get("PowerMetrics", {})
            .get("AverageConsumedWatts")
            if data.get(CAT_THERMAL_POWER, {}).get("power", {}).get("PowerControl")
            else None
        ),
    ),
    RedfishStaticSensorDescription(
        key="system_health",
        name="System Health",
        icon="mdi:heart-pulse",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.get(CAT_SYSTEM, {}).get("Status", {}).get("HealthRollup")
            or data.get(CAT_SYSTEM, {}).get("Status", {}).get("Health")
        ),
    ),
    RedfishStaticSensorDescription(
        key="bios_version",
        name="BIOS Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get(CAT_SYSTEM, {}).get("BiosVersion"),
    ),
    RedfishStaticSensorDescription(
        key="bmc_firmware",
        name="BMC Firmware Version",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.get(CAT_INVENTORY, {}).get("manager", {}).get("FirmwareVersion")
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Redfish sensors and register dynamic discovery listener."""
    coordinator: RedfishDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_ids: set[str] = set()

    # 1. Add static system/chassis sensors
    static_entities: list[SensorEntity] = [
        RedfishStaticSensor(coordinator, desc) for desc in STATIC_SENSORS
    ]
    async_add_entities(static_entities)

    # 2. Dynamic discovery callback for array-based hardware telemetry
    @callback
    def _async_discover_dynamic_sensors() -> None:
        new_entities: list[SensorEntity] = []
        tp_data = coordinator.data.get(CAT_THERMAL_POWER, {})
        thermal = tp_data.get("thermal", {})
        power = tp_data.get("power", {})

        # Temperatures
        for idx, item in enumerate(thermal.get("Temperatures", [])):
            if not _is_populated(item, ACTIVE_SENSOR_STATES):
                continue
            member_key = _get_member_key(item, idx)
            uid = f"temp_{member_key}"
            if uid not in known_ids and item.get("ReadingCelsius") is not None:
                known_ids.add(uid)
                new_entities.append(RedfishTemperatureSensor(coordinator, uid, member_key, item))

        # Fans
        for idx, item in enumerate(thermal.get("Fans", [])):
            if not _is_populated(item, ACTIVE_SENSOR_STATES):
                continue
            member_key = _get_member_key(item, idx)
            uid = f"fan_{member_key}"
            reading = item.get("Reading") if "Reading" in item else item.get("CurrentReading")
            if uid not in known_ids and reading is not None:
                known_ids.add(uid)
                new_entities.append(RedfishFanSensor(coordinator, uid, member_key, item))

        # Chassis Voltages (VRM / Rail monitoring)
        for idx, item in enumerate(power.get("Voltages", [])):
            if not _is_populated(item, ACTIVE_SENSOR_STATES):
                continue
            member_key = _get_member_key(item, idx)
            uid = f"voltage_{member_key}"
            if uid not in known_ids and item.get("ReadingVolts") is not None:
                known_ids.add(uid)
                new_entities.append(RedfishVoltageSensor(coordinator, uid, member_key, item))

        # Individual Power Supplies (PSU Wattage, Line Input Voltage, Health)
        for idx, psu in enumerate(power.get("PowerSupplies", [])):
            if not _is_populated(psu, ACTIVE_PSU_STATES):
                continue
            member_key = _get_member_key(psu, idx)
            psu_name = psu.get("Name") or f"PSU {idx + 1}"

            # PSU Power Output / Input Watts
            uid_watts = f"psu_{member_key}_watts"
            has_watts = any(
                psu.get(k) is not None
                for k in ("PowerOutputWatts", "PowerInputWatts", "LastPowerOutputWatts")
            )
            if uid_watts not in known_ids and has_watts:
                known_ids.add(uid_watts)
                new_entities.append(
                    RedfishPsuMetricSensor(
                        coordinator=coordinator,
                        uid_suffix=uid_watts,
                        member_key=member_key,
                        name=f"{psu_name} Power",
                        device_class=SensorDeviceClass.POWER,
                        unit=UnitOfPower.WATT,
                        extract_fn=lambda p: (
                            p.get("PowerOutputWatts")
                            if p.get("PowerOutputWatts") is not None
                            else p.get("PowerInputWatts", p.get("LastPowerOutputWatts"))
                        ),
                    )
                )

            # PSU Line Input Voltage (Critical for detecting backup leg / UPS drop)
            uid_volts = f"psu_{member_key}_line_voltage"
            if uid_volts not in known_ids and psu.get("LineInputVoltage") is not None:
                known_ids.add(uid_volts)
                new_entities.append(
                    RedfishPsuMetricSensor(
                        coordinator=coordinator,
                        uid_suffix=uid_volts,
                        member_key=member_key,
                        name=f"{psu_name} Line Input Voltage",
                        device_class=SensorDeviceClass.VOLTAGE,
                        unit=UnitOfElectricPotential.VOLT,
                        extract_fn=lambda p: p.get("LineInputVoltage"),
                    )
                )

            # PSU Operational State & Health string
            uid_health = f"psu_{member_key}_status"
            if uid_health not in known_ids:
                known_ids.add(uid_health)
                new_entities.append(
                    RedfishPsuStatusSensor(
                        coordinator=coordinator,
                        uid_suffix=uid_health,
                        member_key=member_key,
                        name=f"{psu_name} Status",
                    )
                )

        # Vendor OEM Dynamic Sensors (HPE iLO, Dell iDRAC, Supermicro)
        oem_sensors = coordinator.data.get(CAT_OEM, {}).get("sensors", {})
        for oem_key, metric in oem_sensors.items():
            uid = f"oem_{oem_key}"
            if uid not in known_ids and metric.value is not None:
                known_ids.add(uid)
                new_entities.append(RedfishOemSensor(coordinator, uid, oem_key))
        
        if new_entities:
            _LOGGER.info(
                "[%s] Dynamically discovered %d Redfish sensor entities",
                coordinator.client.host,
                len(new_entities),
            )
            async_add_entities(new_entities)

    # Run initial discovery and attach listener for future hot-plug/enabled changes
    _async_discover_dynamic_sensors()
    entry.async_on_unload(
        coordinator.async_add_listener(_async_discover_dynamic_sensors)
    )


class RedfishStaticSensor(RedfishEntity, SensorEntity):
    """Representation of a static Redfish sensor."""

    entity_description: RedfishStaticSensorDescription

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        description: RedfishStaticSensorDescription,
    ) -> None:
        """Initialize the static sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)


class RedfishTemperatureSensor(RedfishEntity, SensorEntity):
    """Representation of a dynamic Redfish Temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        uid_suffix: str,
        member_key: str,
        initial_item: dict[str, Any],
    ) -> None:
        """Initialize the temperature sensor."""
        super().__init__(coordinator, uid_suffix)
        self._member_key = member_key
        self._attr_name = initial_item.get("Name") or f"Temperature {member_key}"

    def _find_item(self) -> dict[str, Any] | None:
        temps = (
            self.coordinator.data.get(CAT_THERMAL_POWER, {})
            .get("thermal", {})
            .get("Temperatures", [])
        )
        for idx, item in enumerate(temps):
            if _get_member_key(item, idx) == self._member_key:
                return item
        return None

    @property
    def available(self) -> bool:
        """Return True if sensor is still enabled and populated."""
        item = self._find_item()
        return super().available and item is not None and _is_populated(item, ACTIVE_SENSOR_STATES)

    @property
    def native_value(self) -> float | None:
        """Return current temperature in Celsius."""
        item = self._find_item()
        return item.get("ReadingCelsius") if item else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return threshold attributes for HA alerting."""
        item = self._find_item() or {}
        return {
            "health": item.get("Status", {}).get("Health"),
            "upper_threshold_non_critical": item.get("UpperThresholdNonCritical"),
            "upper_threshold_critical": item.get("UpperThresholdCritical"),
            "upper_threshold_fatal": item.get("UpperThresholdFatal"),
            "physical_context": item.get("PhysicalContext"),
        }


class RedfishFanSensor(RedfishEntity, SensorEntity):
    """Representation of a dynamic Redfish Fan sensor (RPM or Percentage)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:fan"

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        uid_suffix: str,
        member_key: str,
        initial_item: dict[str, Any],
    ) -> None:
        """Initialize the fan sensor."""
        super().__init__(coordinator, uid_suffix)
        self._member_key = member_key
        self._attr_name = initial_item.get("Name") or initial_item.get("FanName") or f"Fan {member_key}"
        units = str(initial_item.get("ReadingUnits", "")).lower()
        if "percent" in units or units == "%":
            self._attr_native_unit_of_measurement = PERCENTAGE
        else:
            self._attr_native_unit_of_measurement = REVOLUTIONS_PER_MINUTE

    def _find_item(self) -> dict[str, Any] | None:
        fans = (
            self.coordinator.data.get(CAT_THERMAL_POWER, {})
            .get("thermal", {})
            .get("Fans", [])
        )
        for idx, item in enumerate(fans):
            if _get_member_key(item, idx) == self._member_key:
                return item
        return None

    @property
    def available(self) -> bool:
        """Return True if fan is populated and enabled."""
        item = self._find_item()
        return super().available and item is not None and _is_populated(item, ACTIVE_SENSOR_STATES)

    @property
    def native_value(self) -> float | int | None:
        """Return fan speed."""
        item = self._find_item()
        if not item:
            return None
        return item.get("Reading") if "Reading" in item else item.get("CurrentReading")


class RedfishVoltageSensor(RedfishEntity, SensorEntity):
    """Representation of a Chassis Voltage rail sensor."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        uid_suffix: str,
        member_key: str,
        initial_item: dict[str, Any],
    ) -> None:
        """Initialize the voltage sensor."""
        super().__init__(coordinator, uid_suffix)
        self._member_key = member_key
        self._attr_name = initial_item.get("Name") or f"Voltage {member_key}"

    def _find_item(self) -> dict[str, Any] | None:
        voltages = (
            self.coordinator.data.get(CAT_THERMAL_POWER, {})
            .get("power", {})
            .get("Voltages", [])
        )
        for idx, item in enumerate(voltages):
            if _get_member_key(item, idx) == self._member_key:
                return item
        return None

    @property
    def available(self) -> bool:
        """Return True if voltage sensor is active."""
        item = self._find_item()
        return super().available and item is not None and _is_populated(item, ACTIVE_SENSOR_STATES)

    @property
    def native_value(self) -> float | None:
        """Return voltage reading."""
        item = self._find_item()
        return item.get("ReadingVolts") if item else None


class RedfishPsuMetricSensor(RedfishEntity, SensorEntity):
    """Representation of an individual PSU wattage or line input voltage sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        uid_suffix: str,
        member_key: str,
        name: str,
        device_class: SensorDeviceClass,
        unit: str,
        extract_fn: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Initialize the PSU metric sensor."""
        super().__init__(coordinator, uid_suffix)
        self._member_key = member_key
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._extract_fn = extract_fn

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
        """Keep sensor available even in StandbyOffline so failover drops to 0V are visible."""
        psu = self._find_psu()
        return super().available and psu is not None and _is_populated(psu, ACTIVE_PSU_STATES)

    @property
    def native_value(self) -> float | int | None:
        """Return PSU metric reading."""
        psu = self._find_psu()
        return self._extract_fn(psu) if psu else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose PSU metadata and failover state."""
        psu = self._find_psu() or {}
        status = psu.get("Status", {})
        return {
            "state": status.get("State"),
            "health": status.get("Health"),
            "model": psu.get("Model"),
            "serial_number": psu.get("SerialNumber"),
            "firmware_version": psu.get("FirmwareVersion"),
            "power_capacity_watts": psu.get("PowerCapacityWatts"),
            "power_supply_type": psu.get("PowerSupplyType"),
            "line_input_voltage_type": psu.get("LineInputVoltageType"),
        }


class RedfishPsuStatusSensor(RedfishEntity, SensorEntity):
    """Diagnostic sensor exposing PSU Health and Failover State (e.g., OK / StandbyOffline)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:power-plug-outline"

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        uid_suffix: str,
        member_key: str,
        name: str,
    ) -> None:
        """Initialize the PSU status sensor."""
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
    def native_value(self) -> str | None:
        """Return combined Health and State for easy automation triggers."""
        psu = self._find_psu()
        if not psu:
            return None
        status = psu.get("Status", {})
        health = status.get("Health") or "Unknown"
        state = status.get("State") or "Unknown"
        if state == "StandbyOffline":
            return f"Standby ({health})"
        return health


class RedfishOemSensor(RedfishEntity, SensorEntity):
    """Representation of a dynamically discovered Vendor OEM Sensor."""

    def __init__(
        self,
        coordinator: RedfishDataUpdateCoordinator,
        uid_suffix: str,
        oem_key: str,
    ) -> None:
        """Initialize the OEM sensor."""
        super().__init__(coordinator, uid_suffix)
        self._oem_key = oem_key
        metric = self._get_metric()
        if metric:
            self._attr_name = metric.name
            self._attr_icon = metric.icon
            self._attr_device_class = metric.device_class
            self._attr_state_class = metric.state_class
            self._attr_native_unit_of_measurement = metric.unit
            self._attr_entity_category = metric.entity_category

    def _get_metric(self) -> Any:
        return self.coordinator.data.get(CAT_OEM, {}).get("sensors", {}).get(self._oem_key)

    @property
    def available(self) -> bool:
        """Return True if the OEM metric is present."""
        return super().available and self._get_metric() is not None

    @property
    def native_value(self) -> Any:
        """Return the current value of the OEM sensor."""
        metric = self._get_metric()
        return metric.value if metric else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return vendor metadata and extra OEM attributes."""
        metric = self._get_metric()
        if not metric:
            return {}
        return {"vendor_extension": metric.vendor, **metric.attributes}