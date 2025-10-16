# 📦 invapp2 — Inventory Management System


**invapp2** is a lightweight inventory management system built with **Flask**, designed to run on edge-friendly hardware such as a **Raspberry Pi**.
It supports tracking items, locations, stock balances, receiving, cycle counts, stock adjustments, transfers, and full transaction history.

> Looking for the quickest path to production? Start with the [hardware recommendations](docs/HARDWARE.md) and then follow the setup guide below.


---

## 📚 Table of Contents
- [Features](#-features)
- [Technology Overview](#-technology-overview)
- [System Requirements](#-system-requirements)
- [Quick Start](#-quick-start)
- [Environment Configuration](#-environment-configuration)
- [Operational Docs](#-operational-docs)
- [Roadmap](#-roadmap)
- [Contributing](#-contributing)
- [License](#-license)

---

## 🚀 Features

- **Item Management** – add, edit, import, and export SKUs (CSV) with minimum-stock thresholds.
- **Location Management** – maintain warehouse/bin codes and descriptions with import/export tooling.
- **Stock Management** – view stock by SKU, batch, and location; perform manual adjustments with a full audit trail; import balances in bulk.
- **Receiving** – capture receipts, auto-generate unique lot numbers (`SKU-YYMMDD-##`), and log purchase order / receiver info.
- **Cycle Counts** – reconcile book vs. physical counts with `CYCLE_COUNT_CONFIRM` and `CYCLE_COUNT_ADJUSTMENT` movement types and CSV exports.
- **Transfers** – move stock between locations while preserving lot/batch tracking.
- **Transaction History** – review every movement with color-coded tables and CSV export.
- **Reports** – `/reports` generates a single ZIP containing Items, Locations, Batches, and Movements.
- **Production Orders** – `/orders` tracks BOMs, routing steps, reservations, shortages, and progress updates.
- **Workstations** – `/work` surfaces workstation queues with ready jobs and still provides a managed document repository for instructions.
- **Settings & Printers** – configure UI theme and Zebra printer host/port with admin authentication.
- **Role-Based Access** – manage user accounts, assign view/edit roles per module, and enforce session timeouts for privileged actions.

---

## 🧱 Technology Overview

| Layer      | Technology |
|------------|------------|
| Backend    | Flask, SQLAlchemy |
| Frontend   | Jinja2 templates (dark-friendly theme) |
| Database   | PostgreSQL (via `psycopg2`) |
| Platform   | Linux edge hardware (Raspberry Pi recommended) |

Project layout highlights:

```
invapp2/
│
├── app.py                # Application entry point
├── invapp/
│   ├── __init__.py       # App factory + blueprint registration
│   ├── models.py         # Database models
│   ├── routes/           # Admin, inventory, orders, printers, reports, settings, work
│   └── templates/        # HTML templates grouped by feature
└── start_inventory.sh    # Helper script for bootstrapping the environment
```

---

## 🖥 Recommended Hardware

invapp2 runs comfortably on inexpensive single-board computers and scales up to
mini-PCs as your facility grows. A quick summary is below—see
[`docs/HARDWARE.md`](docs/HARDWARE.md) for full details and peripheral
recommendations.

| Deployment Size | Suggested Platform | Memory | Storage |
|-----------------|--------------------|--------|---------|
| Pilot / kiosk | Raspberry Pi 4 Model B | 4 GB | 64 GB A2 microSD |
| Small team | Raspberry Pi 4 Model B | 8 GB | 128 GB USB 3.0 SSD |
| Multi-workcell | Intel NUC / Ryzen mini-PC | 16 GB | 256 GB NVMe SSD |

Pair the host with wired Ethernet, a networked Zebra label printer, and USB or
network wedge barcode scanners for the smoothest operator experience.

## ⚡ Setup Instructions


### Software
- Python 3.10+
- PostgreSQL 13 or newer
- `libpq` client libraries (for compiling/installing `psycopg2` on Debian/Raspberry Pi)
- `pip`, `setuptools`, and `wheel`

### Recommended Hardware
For a reliable shop-floor deployment, start with the following baseline. See the [Hardware Guide](docs/hardware-guide.md) for more detailed options and sizing notes.

| Component | Recommendation | Notes |
|-----------|----------------|-------|
| Compute   | Raspberry Pi 4 Model B (4 GB or 8 GB RAM) | Provides enough CPU/RAM headroom for PostgreSQL and Flask. Passive cooling or a small case fan is advised. |
| Storage   | 128 GB+ high-endurance microSD **or** USB SSD | SSD preferred for write-heavy environments; microSD must be industrial/high-endurance grade. |
| Power     | Official Raspberry Pi USB-C power supply | Stable 5V/3A output prevents brownouts during printer bursts. |
| Networking| Wired Ethernet connection | Reduces latency and increases reliability for printer and database traffic. |
| Label Printer | Zebra GK420d / ZD421d (networked) | Works with the Zebra TCP host/port configuration exposed under `/settings/printers`. |
| Optional Peripherals | USB barcode scanner, small touchscreen/monitor | Enhances on-floor data entry and visibility. |

---

## ⚡ Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/YOUR-USERNAME/invapp2.git
   cd invapp2
   ```

2. **Create and activate a virtual environment**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip setuptools wheel
   pip install -r requirements.txt
   ```

   > The application expects PostgreSQL connectivity via `psycopg2-binary`. On Debian/Raspberry Pi systems ensure the `libpq` client libraries are installed (`sudo apt install libpq5 libpq-dev`).

3. Set up your database and environment variables (see [`config.py`](invapp2/config.py) for all available options):

   | Variable | Description | Example |
   |----------|-------------|---------|
   | `DB_URL` | SQLAlchemy connection string for PostgreSQL. | `postgresql+psycopg2://USER:PASSWORD@localhost/invdb` |
   | `SECRET_KEY` | Flask secret used for sessions and CSRF protection. | `export SECRET_KEY="change_me"` |
   | `ADMIN_USER` | Username for the default superuser created on startup. | `export ADMIN_USER="superuser"` |
   | `ADMIN_PASSWORD` | Password applied to the default superuser account. | `export ADMIN_PASSWORD="change_me"` |
   | `ZEBRA_PRINTER_HOST` | Hostname or IP for the Zebra printer service. | `printer.local` |
   | `ZEBRA_PRINTER_PORT` | TCP port that the printer listens on. | `9100` |

   Export the values and initialize the schema:

   ```bash
   export DB_URL="postgresql+psycopg2://USER:PASSWORD@localhost/invdb"
   export SECRET_KEY="change_me"
   export ADMIN_USER="superuser"
   export ADMIN_PASSWORD="change_me"
   export ZEBRA_PRINTER_HOST="printer.local"
   export ZEBRA_PRINTER_PORT=9100

   flask shell
   >>> from invapp.extensions import db
   >>> db.create_all()
   ```

4. **Run the app**
   Launch the production-ready WSGI server with Gunicorn (default bind: `0.0.0.0:8000`):
   ```bash
   gunicorn --bind 0.0.0.0:8000 app:app
   ```

   Or use the helper script which bootstraps a virtual environment, installs dependencies, and launches Gunicorn with sensible defaults:

   ```bash
   ./start_inventory.sh
   ```

### 🪟 Windows bootstrap

Run the bootstrapper from **Command Prompt** (or simply double-click it from File Explorer). The script is implemented purely in batch, so Windows execution policies never get in the way. It will prompt for administrator rights (UAC) when packages need to be installed:

```bat
setup_windows.cmd
```

If Windows reports that the file came from the internet, right-click the script, choose **Properties**, and select **Unblock** before launching it.

Prefer PowerShell? Launch it as administrator and run:

```powershell
powershell -ExecutionPolicy Bypass -File ./setup_windows.ps1
```

If your organization enforces a strict execution policy, temporarily allow scripts in the current session with:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

> If PostgreSQL is already installed or you prefer to manage it manually, append
> the `--skip-postgres` flag (`-SkipPostgres` when using PowerShell) when invoking the script. The bootstrapper also produces an
> `invapp2/.env.local` file with starter environment variables—rename it to `.env`
> (or export the variables) before launching the application.

   > Need a quick development server? You can still run `flask --app app run --debug` locally, but avoid using it in production.

5. **Access the UI** at `http://<host-or-pi-ip>:8000`.

6. **Run automated tests** (optional):
   ```bash
   pytest
   ```

---


## 📚 Additional Documentation

- [`docs/HARDWARE.md`](docs/HARDWARE.md) — Bill of materials and environmental guidance for pilots through multi-workcell deployments.
- [`docs/hardware-guide.md`](docs/hardware-guide.md) — Extended sizing guidance, peripherals, and sourcing suggestions.
- [`docs/deployment-guide.md`](docs/deployment-guide.md) — Provisioning steps, service hardening, and operations checklists.
- [`docs/database-schema.md`](docs/database-schema.md) — Current table definitions and relationships across inventory, production, orders, and access control.

---

## 🔄 Upgrading Existing Installations


All configuration values are read from environment variables with sane defaults defined in [`config.py`](invapp2/config.py).

| Variable | Purpose | Default |
|----------|---------|---------|
| `DB_URL` | SQLAlchemy connection string for PostgreSQL | `postgresql+psycopg2://inv:change_me@localhost/invdb` |
| `SECRET_KEY` | Flask session secret | `supersecret` |
| `ADMIN_USER` | Username for the seed superuser created at startup | `superuser` |
| `ADMIN_PASSWORD` | Password applied to the seed superuser | `joshbaldus` |
| `ADMIN_SESSION_TIMEOUT` | Admin session inactivity timeout (seconds) | `300` |
| `ZEBRA_PRINTER_HOST` | Zebra printer hostname/IP | `localhost` |
| `ZEBRA_PRINTER_PORT` | Zebra printer TCP port | `9100` |

Uploaded work instructions are stored in `invapp/static/work_instructions` and limited to file extensions listed in `Config.WORK_INSTRUCTION_ALLOWED_EXTENSIONS`.

---

## 🛠 Operational Docs
- [Hardware Guide](docs/hardware-guide.md) – Bill of materials, sizing advice, and optional peripherals.
- [Deployment Guide](docs/deployment-guide.md) – Provisioning steps for Raspberry Pi OS, PostgreSQL setup, service hardening, and backup recommendations.

---

## 🗺 Roadmap

- [x] Inventory module (MVP complete)
- [x] Reports module (ZIP export)
- [x] Orders module (BOM authoring, routing progress, material reservations)
- [x] User authentication & admin roles
- [ ] More advanced reporting

---

## 🤝 Contributing

Contributions are welcome!
1. Fork the repo
2. Create a new branch (`git checkout -b feature/your-feature`)
3. Commit changes (`git commit -m "Add feature"`)
4. Push branch (`git push origin feature/your-feature`)
5. Open a Pull Request

---

## 📜 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
