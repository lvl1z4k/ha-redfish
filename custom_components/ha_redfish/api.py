"""Async Redfish API Client supporting Legacy and Modern Subsystem Schemas."""
from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any
import aiohttp
import contextlib

_LOGGER = logging.getLogger(__name__)


class RedfishError(Exception):
    """Base exception for HA Redfish."""


class RedfishAuthError(RedfishError):
    """Raised when BMC authentication fails."""


class RedfishConnectionError(RedfishError):
    """Raised when BMC communication fails."""


class RedfishApiClient:
    """Async Redfish Client for Home Assistant."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        self._session = session
        self.host = host
        self._base_url = f"https://{host}:{port}"
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        if not verify_ssl:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            with contextlib.suppress( ssl.SSLError ):
                ssl_ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
            self._ssl: ssl.SSLContext | bool = ssl_ctx
        else:
            self._ssl = True
        self._auth_lock = asyncio.Lock()

        self._auth_token: str | None = None
        self._session_uri: str | None = None
        self._use_basic_auth: bool = False

        # OData $expand support detected from /redfish/v1
        self.supports_expand: bool = False

        # Discovered Primary Resource URIs
        self.system_uri: str | None = None
        self.chassis_uri: str | None = None
        self.manager_uri: str | None = None

        # Legacy Telemetry URIs
        self.thermal_uri: str | None = None
        self.power_uri: str | None = None

        # Modern Telemetry URIs (Redfish 2020.4+)
        self.thermal_subsystem_uri: str | None = None
        self.power_subsystem_uri: str | None = None
        self.env_metrics_uri: str | None = None
        self.sensors_uri: str | None = None

        # Reserved for future storage collector
        self.storage_uri: str | None = None

        # Discovered Capabilities
        self.reset_target_uri: str | None = None
        self.allowable_reset_types: list[str] = []

    async def async_login(self) -> None:
        """Establish a Redfish session or fall back to Basic Auth."""
        async with self._auth_lock:
            if self._auth_token or self._use_basic_auth:
                return

            session_url = f"{self._base_url}/redfish/v1/SessionService/Sessions/"
            payload = {"UserName": self._username, "Password": self._password}

            try:
                async with self._session.post(
                    session_url,
                    json=payload,
                    ssl=self._ssl,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status in (200, 201):
                        self._auth_token = resp.headers.get("X-Auth-Token")
                        self._session_uri = resp.headers.get("Location")
                        self._use_basic_auth = False
                        _LOGGER.debug("[%s] Redfish session created: %s", self.host, self._session_uri)
                        return
                    if resp.status in (401, 403):
                        raise RedfishAuthError("Invalid BMC credentials")

                    _LOGGER.debug("[%s] SessionService returned %s, using Basic Auth", self.host, resp.status)
                    self._use_basic_auth = True
            except aiohttp.ClientError as err:
                raise RedfishConnectionError(f"Connection failed to {self._base_url}: {err}") from err

    async def async_logout(self) -> None:
        """Delete active Redfish session to keep BMC session table clean."""
        if not self._auth_token or not self._session_uri:
            return

        url = (
            self._session_uri
            if self._session_uri.startswith("http")
            else f"{self._base_url}{self._session_uri}"
        )
        headers = {"X-Auth-Token": self._auth_token}
        try:
            async with self._session.delete(
                url,
                headers=headers,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                _LOGGER.debug("[%s] Logged out Redfish session (HTTP %s)", self.host, resp.status)
        except aiohttp.ClientError as err:
            _LOGGER.debug("[%s] Session logout failed: %s", self.host, err)
        finally:
            self._auth_token = None
            self._session_uri = None

    async def async_request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        """Execute an authenticated HTTP request against the BMC."""
        if not self._auth_token and not self._use_basic_auth:
            await self.async_login()

        url = path if path.startswith("http") else f"{self._base_url}{path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        auth = None

        if self._use_basic_auth:
            auth = aiohttp.BasicAuth(self._username, self._password)
        elif self._auth_token:
            headers["X-Auth-Token"] = self._auth_token

        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                auth=auth,
                json=json_data,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 401 and retry_auth and not self._use_basic_auth:
                    _LOGGER.debug("[%s] Session token expired, re-authenticating", self.host)
                    self._auth_token = None
                    self._session_uri = None
                    await self.async_login()
                    return await self.async_request(method, path, json_data, retry_auth=False)

                if resp.status in (401, 403):
                    raise RedfishAuthError(f"Authentication failed on {path}")

                resp.raise_for_status()
                if resp.status == 204 or resp.content_length == 0:
                    return {}
                return await resp.json()
        except aiohttp.ClientError as err:
            raise RedfishConnectionError(f"Request error on {url}: {err}") from err

    async def async_fetch_collection_members(self, collection_uri: str) -> list[dict[str, Any]]:
        """Fetch all member objects from a Redfish collection, using $expand if supported."""
        if self.supports_expand:
            sep = "&" if "?" in collection_uri else "?"
            expand_uri = f"{collection_uri}{sep}$expand=.($levels=1)"
            try:
                col_data = await self.async_request("GET", expand_uri)
                members = col_data.get("Members", [])
                # Verify the BMC actually expanded the objects beyond bare @odata.id links
                if members and any(len(m.keys()) > 1 for m in members):
                    return members
            except RedfishError:
                _LOGGER.debug("[%s] $expand failed on %s, falling back to member iteration", self.host, collection_uri)

        col_data = await self.async_request("GET", collection_uri)
        member_links = [
            m["@odata.id"] for m in col_data.get("Members", []) if "@odata.id" in m
        ]
        if not member_links:
            return []

        results = await asyncio.gather(
            *(self.async_request("GET", uri) for uri in member_links),
            return_exceptions=True,
        )
        return [res for res in results if isinstance(res, dict) and res]

    async def async_discover_endpoints(self) -> None:
        """Discover standard DMTF collection members and both Legacy & Modern sub-resource URIs."""
        root = await self.async_request("GET", "/redfish/v1/")

        # Check if BMC supports OData $expand
        expand_cfg = root.get("ProtocolFeaturesSupported", {}).get("ExpandQuery", {})
        self.supports_expand = bool(
            expand_cfg.get("ExpandAll") and expand_cfg.get("Levels")
        )

        async def _first_member(key: str) -> str | None:
            if key not in root:
                return None
            col_uri = root[key].get("@odata.id") or root.get("links", {}).get(key, {}).get("href")
            if not col_uri:
                return None
            col = await self.async_request("GET", col_uri)
            members = col.get("Members", [])
            if members and "@odata.id" in members[0]:
                return members[0]["@odata.id"]
            # Fallback for older iLO 4 REST/Redfish hybrid collections
            link_members = col.get("links", {}).get("Member", [])
            if link_members and "href" in link_members[0]:
                return link_members[0]["href"]
            return None

        self.system_uri, self.chassis_uri, self.manager_uri = await asyncio.gather(
            _first_member("Systems"),
            _first_member("Chassis"),
            _first_member("Managers"),
        )

        if not self.system_uri:
            raise RedfishConnectionError("No ComputerSystem resource found in /redfish/v1/Systems")

        if self.chassis_uri:
            chassis = await self.async_request("GET", self.chassis_uri)
            # Legacy endpoints
            self.thermal_uri = chassis.get("Thermal", {}).get("@odata.id")
            self.power_uri = chassis.get("Power", {}).get("@odata.id")
            # Modern Redfish 2020.4+ endpoints
            self.thermal_subsystem_uri = chassis.get("ThermalSubsystem", {}).get("@odata.id")
            self.power_subsystem_uri = chassis.get("PowerSubsystem", {}).get("@odata.id")
            self.env_metrics_uri = chassis.get("EnvironmentMetrics", {}).get("@odata.id")
            self.sensors_uri = chassis.get("Sensors", {}).get("@odata.id")

    async def async_fetch_system(self) -> dict[str, Any]:
        """Fetch ComputerSystem state (Fast cadence: PowerState, Health, Reset Actions)."""
        if not self.system_uri:
            await self.async_discover_endpoints()

        system_data = await self.async_request("GET", self.system_uri)

        actions = system_data.get("Actions", {})
        reset_block = actions.get("#ComputerSystem.Reset", {})
        self.reset_target_uri = reset_block.get("target")

        allowable = reset_block.get("ResetType@Redfish.AllowableValues")
        if allowable:
            self.allowable_reset_types = allowable
        elif not self.allowable_reset_types:
            self.allowable_reset_types = [
                "On",
                "ForceOff",
                "GracefulShutdown",
                "GracefulRestart",
                "ForceRestart",
                "PowerCycle",
            ]

        return system_data

    async def async_fetch_thermal_power(self) -> dict[str, Any]:
        """Fetch Thermal and Power telemetry, normalizing modern Subsystem schemas if needed."""
        out: dict[str, Any] = {"thermal": {}, "power": {}}

        # 1. Try Legacy Thermal & Power first if available
        legacy_tasks: dict[str, Any] = {}
        if self.thermal_uri:
            legacy_tasks["thermal"] = self.async_request("GET", self.thermal_uri)
        if self.power_uri:
            legacy_tasks["power"] = self.async_request("GET", self.power_uri)

        if legacy_tasks:
            results = await asyncio.gather(*legacy_tasks.values(), return_exceptions=True)
            for key, res in zip(legacy_tasks.keys(), results):
                if isinstance(res, dict):
                    out[key] = res

        has_legacy_thermal = bool(
            out["thermal"].get("Temperatures") or out["thermal"].get("Fans")
        )
        has_legacy_power = bool(
            out["power"].get("PowerControl") or out["power"].get("PowerSupplies")
        )

        # 2. If Legacy data is already populated, return immediately to minimize BMC traffic
        if has_legacy_thermal and has_legacy_power:
            return out

        # 3. Poll Modern ThermalSubsystem / PowerSubsystem / EnvironmentMetrics / Sensors
        await self._async_populate_modern_telemetry(
            out,
            need_thermal=not has_legacy_thermal,
            need_power=not has_legacy_power,
        )
        return out

    async def _async_populate_modern_telemetry(
        self,
        out: dict[str, Any],
        need_thermal: bool,
        need_power: bool,
    ) -> None:
        """Fetch modern Redfish 2020.4+ resources and map them to normalized structures."""
        tasks: dict[str, Any] = {}

        if need_thermal and self.thermal_subsystem_uri:
            tasks["thermal_sub"] = self.async_request("GET", self.thermal_subsystem_uri)
        if need_power and self.power_subsystem_uri:
            tasks["power_sub"] = self.async_request("GET", self.power_subsystem_uri)
        if need_power and self.env_metrics_uri:
            tasks["env_metrics"] = self.async_request("GET", self.env_metrics_uri)
        if (need_thermal or need_power) and self.sensors_uri:
            tasks["sensors"] = self.async_fetch_collection_members(self.sensors_uri)

        if not tasks:
            return

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        modern_data = {
            k: v for k, v in zip(tasks.keys(), results) if not isinstance(v, Exception)
        }

        # Preserve raw modern responses for diagnostics.py
        out["modern_raw"] = modern_data

        # Normalize Fans from ThermalSubsystem -> Fans collection
        normalized_fans: list[dict[str, Any]] = []
        thermal_sub = modern_data.get("thermal_sub", {})
        fans_col_uri = thermal_sub.get("Fans", {}).get("@odata.id")
        if need_thermal and fans_col_uri:
            fan_members = await self.async_fetch_collection_members(fans_col_uri)
            for fan in fan_members:
                speed_node = fan.get("SpeedPercent", {})
                reading = speed_node.get("Reading")
                units = "Percent"
                # Some BMCs expose SpeedRPM or SecondaryReading
                if reading is None and "SpeedRPM" in speed_node:
                    reading = speed_node.get("SpeedRPM")
                    units = "RPM"
                normalized_fans.append(
                    {
                        "@odata.id": fan.get("@odata.id"),
                        "MemberId": fan.get("Id"),
                        "Name": fan.get("Name"),
                        "Status": fan.get("Status", {}),
                        "Reading": reading,
                        "ReadingUnits": units,
                        "PhysicalContext": fan.get("PhysicalContext"),
                    }
                )

        # Normalize PowerSupplies from PowerSubsystem -> PowerSupplies collection
        normalized_psus: list[dict[str, Any]] = []
        power_sub = modern_data.get("power_sub", {})
        psus_col_uri = power_sub.get("PowerSupplies", {}).get("@odata.id")
        if need_power and psus_col_uri:
            psu_members = await self.async_fetch_collection_members(psus_col_uri)
            for psu in psu_members:
                metrics: dict[str, Any] = {}
                metrics_uri = psu.get("Metrics", {}).get("@odata.id")
                if metrics_uri:
                    try:
                        metrics = await self.async_request("GET", metrics_uri)
                    except RedfishError:
                        metrics = {}

                out_watts = metrics.get("OutputPowerWatts", {}).get("Reading")
                in_watts = metrics.get("InputPowerWatts", {}).get("Reading")
                in_volts = metrics.get("InputVoltage", {}).get("Reading")

                normalized_psus.append(
                    {
                        "@odata.id": psu.get("@odata.id"),
                        "MemberId": psu.get("Id"),
                        "Name": psu.get("Name"),
                        "Model": psu.get("Model"),
                        "SerialNumber": psu.get("SerialNumber"),
                        "FirmwareVersion": psu.get("FirmwareVersion"),
                        "Status": psu.get("Status", {}),
                        "PowerCapacityWatts": psu.get("PowerCapacityWatts"),
                        "PowerSupplyType": psu.get("PowerSupplyType"),
                        "LineInputVoltageType": psu.get("LineInputVoltageType"),
                        "PowerOutputWatts": out_watts if out_watts is not None else psu.get("PowerOutputWatts"),
                        "PowerInputWatts": in_watts if in_watts is not None else psu.get("PowerInputWatts"),
                        "LineInputVoltage": in_volts if in_volts is not None else psu.get("LineInputVoltage"),
                    }
                )

        # Normalize Temperatures, Voltages, and Power from /Chassis/{id}/Sensors
        normalized_temps: list[dict[str, Any]] = []
        normalized_voltages: list[dict[str, Any]] = []
        sensor_power_watts: float | None = None

        for sensor in modern_data.get("sensors", []):
            r_type = sensor.get("ReadingType")
            r_units = str(sensor.get("ReadingUnits", ""))
            reading = sensor.get("Reading")
            thresholds = sensor.get("Thresholds", {})

            if r_type == "Temperature" or r_units == "Cel":
                normalized_temps.append(
                    {
                        "@odata.id": sensor.get("@odata.id"),
                        "MemberId": sensor.get("Id"),
                        "Name": sensor.get("Name"),
                        "Status": sensor.get("Status", {}),
                        "ReadingCelsius": reading,
                        "PhysicalContext": sensor.get("PhysicalContext"),
                        "UpperThresholdNonCritical": thresholds.get("UpperCaution", {}).get("Reading"),
                        "UpperThresholdCritical": thresholds.get("UpperCritical", {}).get("Reading"),
                        "UpperThresholdFatal": thresholds.get("UpperFatal", {}).get("Reading"),
                    }
                )
            elif r_type == "Voltage" or r_units == "V":
                normalized_voltages.append(
                    {
                        "@odata.id": sensor.get("@odata.id"),
                        "MemberId": sensor.get("Id"),
                        "Name": sensor.get("Name"),
                        "Status": sensor.get("Status", {}),
                        "ReadingVolts": reading,
                    }
                )
            elif (r_type in ("Power", "Rotational", "Percent")) and not normalized_fans:
                if r_type in ("Rotational", "Percent") or r_units in ("RPM", "%"):
                    normalized_fans.append(
                        {
                            "@odata.id": sensor.get("@odata.id"),
                            "MemberId": sensor.get("Id"),
                            "Name": sensor.get("Name"),
                            "Status": sensor.get("Status", {}),
                            "Reading": reading,
                            "ReadingUnits": "Percent" if r_type == "Percent" or r_units == "%" else "RPM",
                        }
                    )
            if r_type == "Power" and sensor.get("PhysicalContext") in ("Chassis", "SystemBoard", None):
                if sensor_power_watts is None and reading is not None:
                    sensor_power_watts = reading

        # Normalize Total Power Consumption from EnvironmentMetrics or Sensors
        env_metrics = modern_data.get("env_metrics", {})
        power_watts_obj = env_metrics.get("PowerWatts", {})
        consumed_watts = power_watts_obj.get("Reading", sensor_power_watts)
        avg_watts = power_watts_obj.get("AverageReading")

        if need_thermal:
            out["thermal"] = {
                "Temperatures": normalized_temps,
                "Fans": normalized_fans,
            }

        if need_power:
            power_control = []
            if consumed_watts is not None or avg_watts is not None:
                power_control.append(
                    {
                        "PowerConsumedWatts": consumed_watts,
                        "PowerMetrics": {"AverageConsumedWatts": avg_watts},
                    }
                )
            out["power"] = {
                "PowerControl": power_control,
                "Voltages": normalized_voltages,
                "PowerSupplies": normalized_psus,
            }

    async def async_fetch_inventory(self) -> dict[str, Any]:
        """Fetch Manager and Chassis metadata (Slow cadence)."""
        tasks: dict[str, Any] = {}
        if self.manager_uri:
            tasks["manager"] = self.async_request("GET", self.manager_uri)
        if self.chassis_uri:
            tasks["chassis"] = self.async_request("GET", self.chassis_uri)

        if not tasks:
            return {"manager": {}, "chassis": {}}

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        out: dict[str, Any] = {}
        for key, res in zip(tasks.keys(), results):
            out[key] = {} if isinstance(res, Exception) else res
        return out

    async def async_reset_system(self, reset_type: str) -> None:
        """Send ComputerSystem.Reset POST command."""
        if not self.reset_target_uri:
            raise RedfishConnectionError("BMC does not expose a ComputerSystem.Reset target URI")
        await self.async_request("POST", self.reset_target_uri, json_data={"ResetType": reset_type})