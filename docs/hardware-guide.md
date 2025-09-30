# 🛠 Hardware Guide

This guide covers recommended hardware for deploying **invapp2** in a small manufacturing or warehouse environment. The goal is a reliable, low-maintenance setup that can live close to the shop floor, integrate with label printers and scanners, and survive 24/7 operation.

---

## 📦 Baseline Bill of Materials

| Role | Recommendation | Why it matters |
|------|----------------|----------------|
| Compute | **Raspberry Pi 4 Model B** (4 GB RAM minimum, 8 GB preferred) | Quad-core ARM CPU provides enough headroom for Flask + PostgreSQL, GPIO-free footprint fits control cabinets. |
| Storage | **USB 3.0 SSD (120–240 GB)** or **industrial/high-endurance microSD (128 GB+)** | Database and audit logs generate sustained writes—SSD maximizes reliability, industrial-grade microSD is a cost-conscious fallback. |
| Power | **Official Raspberry Pi USB-C 5V/3A supply** | Prevents brownouts when peripherals (printers, scanners) draw power; certified for Pi 4 sustained load. |
| Networking | **Wired Ethernet** (Cat6 to switch) | Stable, low-latency connectivity for printers and remote administration. |
| Label Printer | **Zebra GK420d, ZD421d, or other ZPL-compatible networked printer** | Works directly with the Zebra host/port configuration in `/settings/printers`. |
| Barcode Scanner | **USB HID scanner (Honeywell Voyager, Zebra DS2208, etc.)** | Enumerates as a keyboard—no drivers required for the Flask app. |
| Display (optional) | **7"–10" touchscreen** or **HDMI monitor** | Useful for kiosks or staging areas to view stock and work instructions. |
| Protection | **Passive heatsink case** or **low-profile fan HAT** | Keeps Pi 4 cool in enclosures; prevents thermal throttling. |
| Backup Power | **Line-interactive UPS (500–750 VA)** | Allows graceful shutdown during outages and protects the Pi + printer. |

---

## 🧮 Sizing Guidance

- **Concurrent users:** Pi 4 handles 10–15 simultaneous operators. Scale up to an x86 mini PC (Intel NUC, Lenovo Tiny) with 8 GB RAM if you expect heavy concurrent use or plan to run analytics workloads.
- **Database growth:** Inventory movements are append-only. Allocate ~5 GB/year for the PostgreSQL data directory when storing attachments and detailed history. SSD deployments should leave 20% free space for wear leveling.
- **Thermals:** Fanless cases work in climate-controlled areas. In hot shops (>30 °C), use an active-cooled case or mount the Pi in an air-flow path.

---

## 🔌 Peripheral Connectivity

- **Printers:** Configure the Zebra printer with a static IP or DHCP reservation. Confirm TCP port `9100` is reachable from the Pi before setting `ZEBRA_PRINTER_HOST` and `ZEBRA_PRINTER_PORT`.
- **Scanners:** USB barcode scanners behave like keyboards; plug-and-play support means you can scan into any focused input field. For wireless scanners, use the base station connected via USB.
- **Touchscreens:** Raspberry Pi OS detects official DSI touchscreens out-of-the-box. HDMI displays may require `config.txt` adjustments for resolution; keep a USB keyboard available for initial setup.

---

## 🧰 Optional Upgrades

- **External Database Host:** For larger deployments, host PostgreSQL on a separate machine (Intel NUC with NVMe SSD). Point `DB_URL` to that host while keeping the Flask app on the Pi.
- **Industrial Enclosure:** Mount the Pi, power supply, and UPS inside a NEMA-rated enclosure to protect from dust/oil.
- **Monitoring:** Add a small Prometheus node exporter or integrate with Pi-hole/Netdata to observe CPU, RAM, and disk health.

---

## 📋 Maintenance Tips

1. Keep at least one spare microSD/SSD cloned from the production image for quick swap-outs.
2. Schedule weekly database dumps via `pg_dump` to off-device storage (NAS or S3).
3. Update Raspberry Pi OS quarterly (`sudo apt update && sudo apt full-upgrade`) and reboot during planned downtime.
4. Verify label printer calibration and supplies weekly to avoid production stoppages.

These recommendations should keep invapp2 responsive and resilient in day-to-day operations. Adjust component choices based on budget, environmental conditions, and existing infrastructure.
