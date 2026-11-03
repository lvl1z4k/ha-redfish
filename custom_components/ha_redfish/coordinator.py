"""DataUpdateCoordinator with per-category polling intervals."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RedfishApiClient, RedfishAuthError, RedfishConnectionError
from .const import (
    BASE_COORDINATOR_TICK,
    CAT_INVENTORY,
    CAT_SYSTEM,
    CAT_THERMAL_POWER,
    CONF_INTERVAL_INVENTORY,
    CONF_INTERVAL_SYSTEM,
    CONF_INTERVAL_THERMAL_POWER,
    DEFAULT_INTERVAL_INVENTORY,
    DEFAULT_INTERVAL_SYSTEM,
    DEFAULT_INTERVAL_THERMAL_POWER,
    DOMAIN,
    CAT_OEM,
)
from .oem import parse_oem_telemetry

_LOGGER = logging.getLogger(__name__)

CollectorFunc = Callable[[], Coroutine[Any, Any, dict[str, Any]]]


class RedfishDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator managing multiple telemetry categories on independent timers."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: RedfishApiClient,
    ) -> None:
        """Initialize the multi-cadence Redfish coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data['host']}",
            update_interval=timedelta(seconds=BASE_COORDINATOR_TICK),
        )
        self.entry = entry
        self.client = client

        # Persistent merged telemetry cache
        self._cached_data: dict[str, Any] = {
            CAT_SYSTEM: {},
            CAT_THERMAL_POWER: {"thermal": {}, "power": {}},
            CAT_INVENTORY: {"manager": {}, "chassis": {}},
            CAT_OEM: {"sensors": {}, "binary_sensors": {}},
        }

        # Track last execution time (monotonic seconds) per category
        self._last_updated: dict[str, float] = {}

        # Pluggable collector registry (Future storage/OEM collectors register here)
        self._collectors: dict[str, CollectorFunc] = {
            CAT_SYSTEM: self.client.async_fetch_system,
            CAT_THERMAL_POWER: self.client.async_fetch_thermal_power,
            CAT_INVENTORY: self.client.async_fetch_inventory,
        }

    def get_category_interval(self, category: str) -> int:
        """Return configured polling interval in seconds for a category."""
        options = self.entry.options
        mapping = {
            CAT_SYSTEM: options.get(CONF_INTERVAL_SYSTEM, DEFAULT_INTERVAL_SYSTEM),
            CAT_THERMAL_POWER: options.get(
                CONF_INTERVAL_THERMAL_POWER, DEFAULT_INTERVAL_THERMAL_POWER
            ),
            CAT_INVENTORY: options.get(CONF_INTERVAL_INVENTORY, DEFAULT_INTERVAL_INVENTORY),
        }
        return int(mapping.get(category, DEFAULT_INTERVAL_THERMAL_POWER))

    async def async_force_category_refresh(self, category: str) -> None:
        """Invalidate a specific category timer and trigger an immediate coordinator refresh."""
        self._last_updated.pop(category, None)
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        """Run due category collectors and merge into persistent state dictionary."""
        now = time.monotonic()
        due_categories: list[str] = []

        for category in self._collectors:
            last_run = self._last_updated.get(category)
            interval = self.get_category_interval(category)
            if last_run is None or (now - last_run) >= interval:
                due_categories.append(category)

        if not due_categories:
            self._data_changed_this_tick = False
            return self._cached_data

        self._data_changed_this_tick = True

        _LOGGER.debug("[%s] Polling due Redfish categories: %s", self.client.host, due_categories)

        try:
            tasks = [self._collectors[cat]() for cat in due_categories]
            results = await asyncio.gather(*tasks)

            for cat, result in zip(due_categories, results):
                self._cached_data[cat] = result
                self._last_updated[cat] = now

            # Re-evaluate inline Vendor OEM blocks whenever underlying payloads update
            self._cached_data[CAT_OEM] = parse_oem_telemetry(self._cached_data)

        except RedfishAuthError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except RedfishConnectionError as err:
            raise UpdateFailed(f"Error communicating with BMC: {err}") from err

        return self._cached_data


    def async_update_listeners(self) -> None:
        """Only notify entities when a category actually polled new data."""
        if getattr(self, "_data_changed_this_tick", True):
            super().async_update_listeners()