"""Config flow and Options flow for HA Redfish."""
from __future__ import annotations

import logging
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import RedfishApiClient, RedfishAuthError, RedfishConnectionError
from .const import (
    CONF_INTERVAL_INVENTORY,
    CONF_INTERVAL_SYSTEM,
    CONF_INTERVAL_THERMAL_POWER,
    CONF_VERIFY_SSL,
    DEFAULT_INTERVAL_INVENTORY,
    DEFAULT_INTERVAL_SYSTEM,
    DEFAULT_INTERVAL_THERMAL_POWER,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=443): int,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_VERIFY_SSL, default=False): bool,
    }
)


class RedfishConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Redfish."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(
                self.hass, verify_ssl=user_input[CONF_VERIFY_SSL]
            )
            client = RedfishApiClient(
                session=session,
                host=user_input[CONF_HOST],
                port=user_input[CONF_PORT],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                verify_ssl=user_input[CONF_VERIFY_SSL],
            )

            try:
                await client.async_discover_endpoints()
                system_info = await client.async_fetch_system()
            except RedfishAuthError as err:
                _LOGGER.warning("Redfish auth failed for %s: %s", user_input[CONF_HOST], err)
                errors["base"] = "invalid_auth"
            except RedfishConnectionError as err:
                _LOGGER.error("Redfish connection failed for %s: %s", user_input[CONF_HOST], err)
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error during Redfish discovery")
                errors["base"] = "unknown"
            finally:
                await client.async_logout()

            if not errors:
                # Filter out all-zero UUIDs (e.g., 00000000-0000-0000-0000-000000000000)
                raw_uuid = (system_info.get("UUID") or "").strip()
                valid_uuid = (
                    raw_uuid
                    if raw_uuid and raw_uuid.replace("-", "").strip("0")
                    else None
                )
                unique_id = (
                    valid_uuid
                    or (system_info.get("SerialNumber") or "").strip()
                    or user_input[CONF_HOST]
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                model = system_info.get("Model", "Redfish Server")
                hostname = system_info.get("HostName") or user_input[CONF_HOST]
                title = f"{model} ({hostname})"

                return self.async_create_entry(
                    title=title,
                    data=user_input,
                    options={
                        CONF_INTERVAL_SYSTEM: DEFAULT_INTERVAL_SYSTEM,
                        CONF_INTERVAL_THERMAL_POWER: DEFAULT_INTERVAL_THERMAL_POWER,
                        CONF_INTERVAL_INVENTORY: DEFAULT_INTERVAL_INVENTORY,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow for per-category polling configuration."""
        return RedfishOptionsFlowHandler(config_entry)


class RedfishOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle HA Redfish options for category polling intervals."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the category polling intervals."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INTERVAL_SYSTEM,
                    default=options.get(CONF_INTERVAL_SYSTEM, DEFAULT_INTERVAL_SYSTEM),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5, max=300, step=5, unit_of_measurement="s", mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_INTERVAL_THERMAL_POWER,
                    default=options.get(
                        CONF_INTERVAL_THERMAL_POWER, DEFAULT_INTERVAL_THERMAL_POWER
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10, max=600, step=5, unit_of_measurement="s", mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_INTERVAL_INVENTORY,
                    default=options.get(CONF_INTERVAL_INVENTORY, DEFAULT_INTERVAL_INVENTORY),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=60, max=3600, step=30, unit_of_measurement="s", mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)