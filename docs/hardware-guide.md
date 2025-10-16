# 🛠 Hyperion Operations Hub Hardware Guide

This guide expands on the quick reference sheet to help you plan, size, and
maintain hardware for the Hyperion Operations Hub. It assumes on-premises
hosting close to the production floor, with Zebra label printers, barcode
scanners, and workstation displays connecting over the local network.

---

## 1. Baseline Bill of Materials

| Role | Recommendation | Why it Matters |
|------|----------------|----------------|
| Compute | **Raspberry Pi 4 Model B** (4 GB minimum, 8 GB preferred) | Balanced price/performance for Flask + PostgreSQL. Compact footprint fits control cabinets or cart deployments. |
| Storage | **USB 3.0 SSD (120–240 GB)** or **industrial/high-endurance microSD (128 GB+)** | Sustained database writes wear out commodity microSD cards quickly. SSDs offer durability and faster backup/restore performance. |
| Power | **Official Raspberry Pi USB-C 5 V/3 A supply** | Prevents brownouts when scanners and printers draw power spikes. |
| Networking | **Cat6 wired Ethernet** | Stable, low-latency connectivity for database access, printing, and remote administration. |
| Label Printer | **Zebra ZD421d / GK420d / ZT230** | Compatible with Zebra TCP host/port configuration exposed in `/settings/printers`. |
| Barcode Scanner | **USB HID scanners** (Honeywell Voyager, Zebra DS2208, etc.) | Enumerate as keyboards—no drivers required and work with any input field. |
| Display (optional) | **7"–10" touchscreen** or **HDMI monitor** | Turns the hub into a kiosk; useful for work instructions and production dashboards. |
| Protection | **Passive heatsink case** or **low-profile fan HAT** | Keeps Pi 4 deployments cool in enclosed cabinets. |
| Backup Power | **Line-interactive UPS (500–750 VA)** | Allows graceful shutdown during outages and protects sensitive equipment. |

---

## 2. Sizing Guidance

1. **Concurrent Users** – A Pi 4 with 8 GB RAM supports roughly 10–15 simultaneous
   operators. For larger teams or analytics workloads, upgrade to an Intel NUC or
   Ryzen mini PC with 16 GB RAM and NVMe SSD.
2. **Database Growth** – Inventory and production records are append-heavy. Budget
   ~5 GB of PostgreSQL storage per year when capturing attachments and full
   movement history. Maintain at least 20 % free disk space for SSD wear leveling.
3. **Thermals** – Passive cases are fine in climate-controlled areas. If ambient
   temperatures exceed 30 °C (86 °F), add active cooling or move the device into
   an enclosure with forced air.
4. **Networking** – Prefer wired Ethernet wherever possible. If Wi-Fi is the only
   option, use 5 GHz with strong signal strength and keep printers on Ethernet.

---

## 3. Peripheral Connectivity Tips

### Zebra Printers
- Assign static IPs or DHCP reservations so host/port pairs remain predictable.
- Test a sample print from the `/settings/printers` page after provisioning.
- Keep spare label and ribbon stock near the workstation to minimize downtime.

### Barcode Scanners
- USB HID scanners require no configuration—plug in and start scanning into any
  focused input.
- For cordless scanners, pair the base via USB and configure sleep timers to
  conserve battery without dropping connections.
- Network wedge scanners can target webhook endpoints if you prefer hands-free
  receiving workflows.

### Touchscreens & Monitors
- Raspberry Pi OS detects official DSI touchscreens automatically. For HDMI
  displays, adjust `/boot/config.txt` to match native resolution if needed.
- Mount displays at operator height and consider anti-glare covers in bright
  facilities.

---

## 4. Maintenance Playbook

| Cadence | Task | Notes |
|---------|------|-------|
| Daily | Check printers for errors, replenish labels/ribbons. | Prevents job stoppages when print queues spike. |
| Weekly | Export `pg_dump` backups to NAS/S3, verify UPS charge levels. | Use cron on the host or central backup tooling. |
| Monthly | Apply OS/package updates (`sudo apt update && sudo apt full-upgrade`). | Schedule during planned downtime and reboot afterward. |
| Quarterly | Inspect cabling, dust filters, and UPS batteries. | Replace worn cables and clean intake vents for longevity. |
| Semiannually | Clone the system SD/SSD image for cold spares. | Enables rapid swap if the production device fails. |

---

## 5. Upgrade Paths

- **External PostgreSQL** – Move the database to a dedicated x86 server or cloud
  instance. Update `DB_URL` and keep the Flask app on the edge device for local
  printer access.
- **High Availability** – Maintain a warm standby Pi with nightly database
  restores. Promote during maintenance windows or unexpected outages.
- **Monitoring** – Deploy lightweight collectors (Netdata, Prometheus node
  exporter, Grafana Agent) to track CPU, memory, disk, and printer status.
- **Industrial Enclosures** – In dusty or oily environments, mount hardware in
  NEMA-rated enclosures with filtered airflow.

With the right hardware foundation, Hyperion Operations Hub delivers
low-maintenance operations management while remaining flexible enough to grow
alongside your facility.
