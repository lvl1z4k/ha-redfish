"""Diagnostics support for the HA Redfish integration."""
from __future__ import annotations

import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import (
    CAT_INVENTORY,
    CAT_SYSTEM,
    CAT_THERMAL_POWER,
    DOMAIN,
)
from .coordinator import RedfishDataUpdateCoordinator

# Keys to redact across both config_entry and nested Redfish JSON payloads
TO_REDACT: set[str] = {
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    "UUID",
    "SerialNumber",
    "PartNumber",
    "AssetTag",
    "HostName",
    "MACAddress",
    "PermanentMACAddress",
    "IPv4Addresses",
    "IPv6Addresses",
    "IPv4StaticAddresses",
    "iSCSIBoot",
    "ServiceEntryPointUUID",
    "X-Auth-Token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: RedfishDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    client = coordinator.client
    now = time.monotonic()

    # Calculate seconds since each category was last polled
    category_age: dict[str, float | None] = {}
    for cat in (CAT_SYSTEM, CAT_THERMAL_POWER, CAT_INVENTORY):
        last_ts = coordinator._last_updated.get(cat)
        category_age[cat] = round(now - last_ts, 1) if last_ts is not None else None

    diagnostics_payload: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "client_discovery": {
            "use_basic_auth": client._use_basic_auth,
            "has_active_session": client._auth_token is not None,
            "supports_odata_expand": client.supports_expand,
            "system_uri": client.system_uri,
            "chassis_uri": client.chassis_uri,
            "manager_uri": client.manager_uri,
            "thermal_uri": client.thermal_uri,
            "power_uri": client.power_uri,
            "thermal_subsystem_uri": client.thermal_subsystem_uri,
            "power_subsystem_uri": client.power_subsystem_uri,
            "env_metrics_uri": client.env_metrics_uri,
            "sensors_uri": client.sensors_uri,
            "storage_uri": client.storage_uri,
            "reset_target_uri": client.reset_target_uri,
            "allowable_reset_types": client.allowable_reset_types,
        },
        "coordinator_schedule": {
            "configured_intervals_sec": {
                CAT_SYSTEM: coordinator.get_category_interval(CAT_SYSTEM),
                CAT_THERMAL_POWER: coordinator.get_category_interval(CAT_THERMAL_POWER),
                CAT_INVENTORY: coordinator.get_category_interval(CAT_INVENTORY),
            },
            "seconds_since_last_poll": category_age,
        },
        "redfish_telemetry_cache": async_redact_data(coordinator.data, TO_REDACT),
    }

    return diagnostics_payload