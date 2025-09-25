# Hardware Recommendations for invapp2

The **invapp2** inventory management system is optimized for small-to-medium
warehouses that want a low-cost but reliable edge deployment. The guidance
below assumes on-premises hosting inside the facility so barcode scanners and
label printers can connect over the local network.

## Core Compute Platform

| Deployment Size | Recommended Board | CPU | Memory | Storage | Notes |
|-----------------|-------------------|-----|--------|---------|-------|
| Pilot / Single workstation | Raspberry Pi 4 Model B | Quad-core Cortex-A72 @ 1.5 GHz | 4 GB | 64 GB Class 10 microSD (A2) | Active cooling recommended. |
| Small team (≤10 concurrent users) | Raspberry Pi 4 Model B | Quad-core Cortex-A72 @ 1.8 GHz (overclock) | 8 GB | 128 GB NVMe via USB 3.0 enclosure | Improves database I/O; keep a fan case installed. |
| Medium deployment (multiple workcells) | Intel NUC 11 or equivalent mini-PC | Quad-core i5 or Ryzen 5 | 16 GB | 256 GB NVMe SSD | Allows running PostgreSQL, application, and monitoring stack on one host. |

> **Tip:** When using Raspberry Pi hardware, flash the storage with Raspberry
> Pi OS Lite (64-bit) and run `sudo raspi-config` to expand the filesystem and
> enable SSH for headless maintenance.

## Network & Peripherals

- **Wired Ethernet (preferred):** Hardwire the host into the facility switch to
  reduce latency for barcode scanners and label printers.
- **Wi-Fi:** Only recommended for pilot installations; use 5 GHz where possible.
- **Label Printer:** Zebra ZD420 or ZT230 series with network card. Configure
  the host and printer to static IPs within the same subnet for predictable
  connections.
- **Barcode Scanners:** USB HID or network wedge scanners that can post to the
  application. Devices tested internally include Zebra DS2208 (USB) and
  Honeywell Xenon 1950g.
- **Uninterruptible Power Supply (UPS):** A 500–750 VA UPS keeps the database
  from abrupt power loss and gives time to shut down safely.

## Optional Enhancements

- **External PostgreSQL Server:** For installations that already run a central
  database, point `DB_URL` to the managed instance. Ensure low-latency
  connectivity to keep transaction processing snappy.
- **Monitoring:** Deploy lightweight monitoring such as Netdata or Grafana
  Agent to track CPU, memory, and disk health on the host.
- **Backup Strategy:** Schedule nightly `pg_dump` exports to an off-host NAS or
  S3-compatible object store for disaster recovery.

## Environmental Considerations

- Maintain ambient temperature between 10–35 °C (50–95 °F).
- Keep devices in dust-resistant enclosures if the warehouse produces
  particulate matter.
- Prefer fan-assisted cases in non-climate-controlled spaces.

These recommendations provide a balanced bill of materials for running invapp2
while preserving room for growth as the operation scales.
