"""Constants for the HA Redfish integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ha_redfish"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Configuration Keys
CONF_VERIFY_SSL = "verify_ssl"
CONF_INTERVAL_SYSTEM = "interval_system"
CONF_INTERVAL_THERMAL_POWER = "interval_thermal_power"
CONF_INTERVAL_INVENTORY = "interval_inventory"

# Default Polling Intervals (in seconds)
BASE_COORDINATOR_TICK = 5
DEFAULT_INTERVAL_SYSTEM = 15          # Power state, System health, Indicator LED
DEFAULT_INTERVAL_THERMAL_POWER = 30   # Temps, Fans, Power watts, PSU status
DEFAULT_INTERVAL_INVENTORY = 300      # BMC Manager info, BIOS version, static health

# Telemetry Category Keys
CAT_SYSTEM = "system"
CAT_THERMAL_POWER = "thermal_power"
CAT_INVENTORY = "inventory"
CAT_OEM = "oem"

# Standard DMTF Reset Types
RESET_ON = "On"
RESET_FORCE_OFF = "ForceOff"
RESET_GRACEFUL_SHUTDOWN = "GracefulShutdown"
RESET_GRACEFUL_RESTART = "GracefulRestart"
RESET_FORCE_RESTART = "ForceRestart"
RESET_POWER_CYCLE = "PowerCycle"
RESET_NMI = "Nmi"
