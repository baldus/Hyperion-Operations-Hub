[README.md](https://github.com/user-attachments/files/22282410/README.md)
# 📦 invapp2 — Inventory Management System  

**invapp2** is a lightweight inventory management system built with **Flask**, designed to run on edge-friendly hardware such as a **Raspberry Pi**.
It supports tracking items, locations, stock balances, receiving, cycle counts, stock adjustments, transfers, and full transaction history.

> Looking for the quickest path to production? Start with the [hardware recommendations](docs/HARDWARE.md) and then follow the setup guide below.

---

## 🚀 Features

- **Item Management**
  - Add, edit, import, and export items (CSV).
  - Define min stock levels per SKU.

- **Location Management**
  - Add, import, and export storage locations.
  - Codes and descriptions for warehouse/bin tracking.

- **Stock Management**
  - View stock by SKU, batch, and location.
  - Manual adjustments with audit trail.
  - Bulk stock imports via CSV.
  - Export stock balances.

- **Receiving**
  - Receive items into stock.
  - Auto-generate unique lot/batch numbers (`SKU-YYMMDD-##`).
  - Record PO numbers and receiving personnel.

- **Cycle Counts**
  - Log physical counts vs. book stock.
  - Movement types: `CYCLE_COUNT_CONFIRM` and `CYCLE_COUNT_ADJUSTMENT`.
  - Export cycle counts to CSV.

- **Transfers (Move)**
  - Move stock between locations while preserving lot/batch tracking.

- **Transaction History**
  - Full log of all stock movements.
  - Exportable to CSV.
  - Color-coded and striped UI for readability.

- **Reports (`/reports`)**
  - One-click export of all tables (Items, Locations, Batches, Movements) into a single ZIP.

- **Production Orders (`/orders`)**
  - Track open, scheduled, and closed production orders with BOM and routing details.
  - Validate BOM components, plan routing steps, and automatically reserve material when available.
  - Update routing progress, manage order status changes, and audit shortages.

- **Work Instructions (`/work`)**
  - Upload PDF/allowed documents to a managed directory for shop-floor use.
  - Admins can remove outdated instructions; all users can view the catalog.

- **Settings & Printers (`/settings`, `/settings/printers`)**
  - Toggle between dark and light UI themes per session.
  - Configure Zebra printer host/port values after admin authentication.

- **Admin Tools (`/admin`)**
  - Privileged login/logout flow for unlocking admin-only features.
  - Automatic admin session timeout enforcement for security.

---

## 🛠 Tech Stack  

- **Backend**: Flask, SQLAlchemy  
- **Database**: PostgreSQL  
- **Frontend**: Jinja2 templates (dark theme, bubble-style buttons)  
- **Platform**: Raspberry Pi (Linux)  

---

## 📂 Project Structure  

```
invapp2/
│
├── app.py                # Application entry point
├── invapp/
│   ├── __init__.py       # App factory + blueprint registration
│   ├── models.py         # Database models
│   ├── routes/
│   │   ├── admin.py      # Admin login/session management
│   │   ├── inventory.py  # Inventory and stock operations
│   │   ├── orders.py     # Production orders with BOM, routing, reservations
│   │   ├── printers.py   # Zebra printer configuration UI
│   │   ├── reports.py    # CSV/ZIP reporting endpoints
│   │   ├── settings.py   # Theme toggles and settings landing page
│   │   └── work.py       # Work instruction upload/listing views
│   └── templates/
│       ├── inventory/    # Inventory HTML templates
│       ├── orders/       # Orders pages (home, detail, forms)
│       ├── reports/      # Reports HTML templates
│       ├── settings/     # Settings and printer management views
│       └── work/         # Work instruction browser
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

1. Clone the repo:  
   ```bash
   git clone https://github.com/YOUR-USERNAME/invapp2.git
   cd invapp2
   ```

2. Create a virtual environment & install Python dependencies from `invapp2/requirements.txt`:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   # upgrade packaging tools on fresh environments
   pip install --upgrade pip setuptools wheel
   pip install -r requirements.txt
   ```

   > **Note:** The app expects PostgreSQL connectivity via `psycopg2-binary`. On Debian/Raspberry Pi systems ensure the `libpq` client libraries are installed (e.g. `sudo apt install libpq5`).

3. Set up your database and environment variables (see [`config.py`](invapp2/config.py) for all available options):

   | Variable | Description | Example |
   |----------|-------------|---------|
   | `DB_URL` | SQLAlchemy connection string for PostgreSQL. | `postgresql+psycopg2://USER:PASSWORD@localhost/invdb` |
   | `SECRET_KEY` | Flask secret used for sessions and CSRF protection. | `export SECRET_KEY="change_me"` |
   | `ZEBRA_PRINTER_HOST` | Hostname or IP for the Zebra printer service. | `printer.local` |
   | `ZEBRA_PRINTER_PORT` | TCP port that the printer listens on. | `9100` |

   Export the values and initialize the schema:
   ```bash
   export DB_URL="postgresql+psycopg2://USER:PASSWORD@localhost/invdb"
   export SECRET_KEY="change_me"
   export ZEBRA_PRINTER_HOST="printer.local"  # network address of your Zebra printer
   export ZEBRA_PRINTER_PORT=9100              # port for the printer connection
   flask shell
   >>> from invapp.extensions import db
   >>> db.create_all()
   ```

4. Run the app:
   ```bash
   flask run --host=0.0.0.0 --port=5000
   ```

   Or use the convenience launcher which will bootstrap a virtual environment,
   install the dependencies from `requirements.txt`, and then start the
   application:

   ```bash
   ./start_inventory.sh
   ```

5. Access via browser:
   ```
   http://<raspberry-pi-ip>:5000
   ```

6. (Optional) Run the automated tests once the app dependencies are installed:
   ```bash
   pytest
   ```

---

## 📚 Additional Documentation

- [`docs/HARDWARE.md`](docs/HARDWARE.md) — Bill of materials and environmental
  guidance for pilots through multi-workcell deployments.
- _Coming soon:_ Deployment runbook and database backup checklist.

---

## 🔄 Upgrading Existing Installations

Existing deployments created before the introduction of item notes need a one-time
database migration. Run the following SQL against your production database (or
allow the application to run once so it can apply the change automatically):

```sql
ALTER TABLE item ADD COLUMN notes TEXT;
```

---

## 🗺 Roadmap

- [x] Inventory module (MVP complete)
- [x] Reports module (ZIP export)
- [x] Orders module (BOM authoring, routing progress, material reservations)
- [ ] User authentication & admin roles  
- [ ] More advanced reporting  

---

## 🤝 Contributing  

Contributions are welcome!  
- Fork the repo  
- Create a new branch (`git checkout -b feature/your-feature`)  
- Commit changes (`git commit -m "Add feature"`)  
- Push branch (`git push origin feature/your-feature`)  
- Open a Pull Request  

---

## 📜 License  

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.  
