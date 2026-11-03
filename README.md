# HA Redfish

A multi-vendor DMTF Redfish integration for Home Assistant supporting local polling, power control, and hardware telemetry across enterprise BMCs (HPE iLO 4/5/6, Dell iDRAC 8/9/10, Supermicro, Lenovo XClarity, and OpenBMC).

## Features
- **Power & Chassis Control:** Server power switch (`On` / `GracefulShutdown`), Chassis UID Indicator LED control, and stateless reset buttons (`GracefulRestart`, `ForceRestart`, `ForceOff`, `PowerCycle`, `NMI`).
- **Multi-Cadence Telemetry Polling:** Independent configurable polling intervals for System State (default 15s), Thermal & Power (default 30s), and Hardware Inventory (default 300s).
- **Dynamic Sensor & Dual-PSU Failover Discovery:** Automatically skips unpopulated (`Absent`/`Disabled`) slots while monitoring standby/failover PSUs (`StandbyOffline`) for input voltage loss (`0V`) and health faults. Includes a **Rediscover Hardware & Sensors** button for hot-plugged components.
- **Hybrid Schema & OEM Support:** Supports both legacy (`Thermal`/`Power`) and modern (`ThermalSubsystem`/`PowerSubsystem`/`EnvironmentMetrics`) Redfish schemas, plus inline vendor OEM health rollups (`Oem.Hpe`/`Oem.Hp`, `Oem.Dell`, `Oem.Supermicro`).

## Installation via HACS
1. Open **HACS** in Home Assistant.
2. Click the three-dots menu in the top right and select **Custom repositories**.
3. Enter the URL of this repository and select **Integration** as the category.
4. Click **Download** on the **HA Redfish** card and restart Home Assistant.
5. Navigate to **Settings -> Devices & Services -> Add Integration** and search for **HA Redfish**.
