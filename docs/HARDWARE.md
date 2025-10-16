# Hyperion Operations Hub Hardware Quick Reference

This quick reference summarizes recommended hardware profiles for running the
Hyperion Operations Hub. Use it to choose the right platform before diving into
the more detailed [Hardware Guide](hardware-guide.md).

## Core Deployment Tiers

| Deployment Tier | Recommended Platform | CPU / RAM | Storage | Notes |
|-----------------|----------------------|-----------|---------|-------|
| Pilot / Kiosk | Raspberry Pi 4 Model B | Quad-core Cortex-A72 · 4 GB | 64 GB A2 microSD | Great for proof-of-concept stations or single workcells. Add a heatsink case and reliable power supply. |
| Small Team (≤10 users) | Raspberry Pi 4 Model B | Quad-core Cortex-A72 · 8 GB | 128 GB USB 3.0 SSD | SSD improves database durability and throughput. Pair with wired Ethernet and a 500 VA UPS. |
| Multi-Workcell | Intel NUC / Ryzen mini PC | Quad-core i5/Ryzen 5 · 16 GB | 256 GB NVMe SSD | Supports PostgreSQL + app + monitoring stack with headroom. Ideal when multiple printers/workstations connect concurrently. |
| Centralized / HQ | Rack-mount or VM host | vCPUs (4+) · 16–32 GB | Redundant SSD/NVMe | Run PostgreSQL centrally and deploy multiple gunicorn workers. Integrate with enterprise backups and monitoring. |

> **Tip:** Regardless of platform, prioritize wired Ethernet and high-quality
power. Label printers and barcode scanners behave more reliably with stable
network connectivity.

## Peripheral Checklist

- **Label Printers:** Zebra ZD421d, GK420d, or other ZPL-compatible printers.
  Configure each printer with a static IP so it matches the Zebra host/port
  settings in `/settings/printers`.
- **Barcode Scanners:** USB HID scanners (Zebra DS2208, Honeywell Xenon 1950g)
  work out-of-the-box. For cordless scanners, connect the base station over USB.
- **Displays:** 7"–10" touchscreens or HDMI monitors help transform the Hub into
  a workstation kiosk. Keep a USB keyboard handy for maintenance tasks.
- **Power Protection:** A 500–750 VA UPS gives you time to shut down PostgreSQL
  and prevents printer power dips from rebooting the host.

## Environmental Considerations

- Maintain an ambient temperature between 10–35 °C (50–95 °F).
- Install passive heatsinks or fan cases for Raspberry Pi deployments in
  non-climate-controlled spaces.
- Shield devices from dust and debris; industrial enclosures are recommended for
  harsh environments.

For sizing guidance, maintenance routines, and upgrade paths refer to the
[full hardware guide](hardware-guide.md).
