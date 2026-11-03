"""Pluggable Vendor OEM Parser for HPE iLO, Dell iDRAC, and Supermicro."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfPower


@dataclass
class OemSensorMetric:
    """Normalized representation of a vendor-specific OEM sensor."""

    key: str
    name: str
    value: Any
    vendor: str
    icon: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    unit: str | None = None
    entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class OemBinarySensorMetric:
    """Normalized representation of a vendor-specific OEM binary sensor."""

    key: str
    name: str
    is_on: bool
    vendor: str
    device_class: BinarySensorDeviceClass = BinarySensorDeviceClass.PROBLEM
    icon: str | None = None
    entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC
    attributes: dict[str, Any] = field(default_factory=dict)


def parse_oem_telemetry(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Inspect cached Redfish payloads and extract normalized OEM sensors."""
    sensors: dict[str, OemSensorMetric] = {}
    binary_sensors: dict[str, OemBinarySensorMetric] = {}

    system_oem = data.get("system", {}).get("Oem", {})
    power_oem = data.get("thermal_power", {}).get("power", {}).get("Oem", {})
    thermal_oem = data.get("thermal_power", {}).get("thermal", {}).get("Oem", {})
    chassis_oem = data.get("inventory", {}).get("chassis", {}).get("Oem", {})
    manager_oem = data.get("inventory", {}).get("manager", {}).get("Oem", {})

    # 1. HPE iLO (Oem.Hpe for Gen10+ / iLO 5+, Oem.Hp for Gen9 / iLO 4)
    hpe_sys = system_oem.get("Hpe") or system_oem.get("Hp")
    if isinstance(hpe_sys, dict):
        _parse_hpe_oem(
            hpe_sys,
            power_oem.get("Hpe") or power_oem.get("Hp") or {},
            manager_oem.get("Hpe") or manager_oem.get("Hp") or {},
            sensors,
            binary_sensors,
        )

    # 2. Dell iDRAC (Oem.Dell)
    dell_sys = system_oem.get("Dell")
    if isinstance(dell_sys, dict):
        _parse_dell_oem(
            dell_sys,
            chassis_oem.get("Dell") or {},
            power_oem.get("Dell") or {},
            sensors,
            binary_sensors,
        )

    # 3. Supermicro (Oem.Supermicro)
    smc_sys = (
        system_oem.get("Supermicro")
        or thermal_oem.get("Supermicro")
        or chassis_oem.get("Supermicro")
    )
    if isinstance(smc_sys, dict):
        _parse_supermicro_oem(
            system_oem.get("Supermicro") or {},
            thermal_oem.get("Supermicro") or {},
            sensors,
            binary_sensors,
        )

    return {
        "sensors": sensors,
        "binary_sensors": binary_sensors,
    }


def _parse_hpe_oem(
    sys_oem: dict[str, Any],
    pwr_oem: dict[str, Any],
    mgr_oem: dict[str, Any],
    sensors: dict[str, OemSensorMetric],
    binary_sensors: dict[str, OemBinarySensorMetric],
) -> None:
    """Extract HPE iLO 4 / 5 / 6 specific telemetry."""
    vendor = "HPE iLO"

    # Server POST State (e.g., FinishedPost, InPost, PowerOff, InPostDiscoveryComplete)
    post_state = sys_oem.get("PostState")
    if post_state is not None:
        sensors["hpe_post_state"] = OemSensorMetric(
            key="hpe_post_state",
            name="iLO POST State",
            value=post_state,
            vendor=vendor,
            icon="mdi:console-line",
            entity_category=None,
        )

    # Power Regulator Mode (OSControl, DynamicPowerSavings, StaticLowPower, StaticHighPerformance)
    pwr_reg_modes = sys_oem.get("PowerRegulatorModesSupported")
    pwr_reg_mode = sys_oem.get("PowerRegulatorMode")
    if pwr_reg_mode is not None:
        sensors["hpe_power_regulator_mode"] = OemSensorMetric(
            key="hpe_power_regulator_mode",
            name="Power Regulator Mode",
            value=pwr_reg_mode,
            vendor=vendor,
            icon="mdi:tune-vertical",
            attributes={"supported_modes": pwr_reg_modes} if pwr_reg_modes else {},
        )

    # Aggregate Health Status Rollups (Storage, Memory, Fans, PSUs, AMS, BIOS)
    agg_health = sys_oem.get("AggregateHealthStatus", {})
    if isinstance(agg_health, dict):
        # Agentless Management Service (AMS) State
        ams_state = agg_health.get("AgentlessManagementService")
        if isinstance(ams_state, str):
            sensors["hpe_ams_status"] = OemSensorMetric(
                key="hpe_ams_status",
                name="Agentless Management Service",
                value=ams_state,
                vendor=vendor,
                icon="mdi:cog-sync-outline",
            )

        rollup_targets = {
            "Storage": ("hpe_storage_health", "Storage Subsystem", "mdi:harddisk"),
            "Memory": ("hpe_memory_health", "Memory Subsystem", "mdi:memory"),
            "Network": ("hpe_network_health", "Network Subsystem", "mdi:lan"),
            "BiosOrHardwareHealth": ("hpe_bios_hw_health", "BIOS & Hardware", "mdi:chip"),
            "PowerSupplies": ("hpe_psu_health_rollup", "Power Supplies Rollup", "mdi:power-plug"),
            "Fans": ("hpe_fans_health_rollup", "Fans Rollup", "mdi:fan"),
        }
        for hpe_key, (metric_key, label, icon) in rollup_targets.items():
            node = agg_health.get(hpe_key)
            if isinstance(node, dict):
                health = (node.get("Status", {}).get("Health") or "OK").upper()
                extra: dict[str, Any] = {"health": health}
                if hpe_key == "PowerSupplies" and "PowerSuppliesMismatch" in agg_health:
                    extra["psu_mismatch"] = agg_health.get("PowerSuppliesMismatch")

                binary_sensors[metric_key] = OemBinarySensorMetric(
                    key=metric_key,
                    name=f"{label} Problem",
                    is_on=(health != "OK"),
                    vendor=vendor,
                    icon=icon,
                    attributes=extra,
                )

        # Fan & Power Supply Redundancy states (Redundant vs NonRedundant / Failed)
        for red_key, metric_key, label in (
            ("FanRedundancy", "hpe_fan_redundancy_lost", "Fan Redundancy"),
            ("PowerSupplyRedundancy", "hpe_psu_redundancy_lost", "PSU Redundancy"),
        ):
            red_val = agg_health.get(red_key)
            if isinstance(red_val, str):
                binary_sensors[metric_key] = OemBinarySensorMetric(
                    key=metric_key,
                    name=f"{label} Lost",
                    is_on=(red_val.lower() not in ("redundant", "fullyredundant")),
                    vendor=vendor,
                    attributes={"redundancy_state": red_val},
                )

    # SmartStorage Energy Pack / Cache Battery
    batteries = sys_oem.get("Battery", [])
    if isinstance(batteries, list):
        for idx, bat in enumerate(batteries):
            if not isinstance(bat, dict) or bat.get("Present") not in ("Yes", True):
                continue
            bat_index = bat.get("Index", idx + 1)
            health = (bat.get("Condition") or "OK").upper()
            key = f"hpe_smart_storage_battery_{bat_index}"
            binary_sensors[key] = OemBinarySensorMetric(
                key=key,
                name=f"SmartStorage Energy Pack {bat_index} Fault",
                is_on=(health != "OK"),
                vendor=vendor,
                device_class=BinarySensorDeviceClass.BATTERY,
                attributes={
                    "condition": bat.get("Condition"),
                    "model": bat.get("Model"),
                    "firmware_version": bat.get("FirmwareVersion"),
                    "max_cap_watts": bat.get("MaxCapWatts"),
                    "error_code": bat.get("ErrorCode"),
                },
            )

    # HPE Power OEM High-Efficiency Mode & Peak / Idle Power
    if isinstance(pwr_oem, dict):
        he_mode = pwr_oem.get("HighEfficiencyMode")
        if he_mode is not None:
            sensors["hpe_psu_high_efficiency_mode"] = OemSensorMetric(
                key="hpe_psu_high_efficiency_mode",
                name="PSU High Efficiency Mode",
                value=he_mode,
                vendor=vendor,
                icon="mdi:leaf-circle-outline",
            )


def _parse_dell_oem(
    sys_oem: dict[str, Any],
    chassis_oem: dict[str, Any],
    pwr_oem: dict[str, Any],
    sensors: dict[str, OemSensorMetric],
    binary_sensors: dict[str, OemBinarySensorMetric],
) -> None:
    """Extract Dell iDRAC 8 / 9 / 10 specific telemetry."""
    vendor = "Dell iDRAC"
    dell_sys = sys_oem.get("DellSystem", {})
    if not isinstance(dell_sys, dict):
        return

    # Estimated System Airflow in CFM
    airflow = dell_sys.get("EstimatedSystemAirflowCFM")
    if airflow is not None:
        sensors["dell_airflow_cfm"] = OemSensorMetric(
            key="dell_airflow_cfm",
            name="Estimated System Airflow",
            value=airflow,
            vendor=vendor,
            unit="CFM",
            icon="mdi:weather-windy",
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=None,
        )

    # Exhaust Temperature
    exhaust_temp = dell_sys.get("EstimatedExhaustTemperatureCel")
    if exhaust_temp is not None:
        sensors["dell_exhaust_temp"] = OemSensorMetric(
            key="dell_exhaust_temp",
            name="Estimated Exhaust Temperature",
            value=exhaust_temp,
            vendor=vendor,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            unit="°C",
            entity_category=None,
        )

    # Subsystem Rollup Statuses in DellSystem
    dell_rollups = {
        "StorageRollupStatus": ("dell_storage_rollup", "Storage Subsystem", "mdi:harddisk"),
        "BatteryRollupStatus": ("dell_cmos_battery_rollup", "CMOS Battery", "mdi:battery-heart-variant"),
        "IntrusionRollupStatus": ("dell_intrusion_rollup", "Chassis Intrusion", "mdi:shield-lock-outline"),
        "LicensingRollupStatus": ("dell_licensing_rollup", "iDRAC Licensing", "mdi:key-variant"),
        "SELRollupStatus": ("dell_sel_rollup", "System Event Log (SEL)", "mdi:text-box-check-outline"),
        "VoltRollupStatus": ("dell_volt_rollup", "Voltage Rails Rollup", "mdi:flash-triangle-outline"),
        "TempRollupStatus": ("dell_temp_rollup", "Thermal Rollup", "mdi:thermometer-alert"),
    }

    for dell_key, (metric_key, label, icon) in dell_rollups.items():
        val = dell_sys.get(dell_key)
        if isinstance(val, str):
            binary_sensors[metric_key] = OemBinarySensorMetric(
                key=metric_key,
                name=f"{label} Problem",
                is_on=(val.upper() != "OK"),
                vendor=vendor,
                icon=icon,
                attributes={"rollup_status": val},
            )


def _parse_supermicro_oem(
    sys_oem: dict[str, Any],
    thermal_oem: dict[str, Any],
    sensors: dict[str, OemSensorMetric],
    binary_sensors: dict[str, OemBinarySensorMetric],
) -> None:
    """Extract Supermicro Redfish OEM telemetry."""
    vendor = "Supermicro"

    fan_mode = thermal_oem.get("FanMode") or sys_oem.get("FanMode")
    if isinstance(fan_mode, str):
        sensors["smc_fan_mode"] = OemSensorMetric(
            key="smc_fan_mode",
            name="BMC Fan Profile",
            value=fan_mode,
            vendor=vendor,
            icon="mdi:fan-auto",
        )