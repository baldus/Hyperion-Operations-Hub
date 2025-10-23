# 🚀 Hyperion Operations Hub

Hyperion Operations Hub is a Flask-based operations platform that unifies
inventory control, production reporting, workstation management, and printer
integrations for small-to-medium manufacturers. The application is designed to
run comfortably on edge hardware such as a Raspberry Pi while still scaling to
x86 mini PCs and centralized PostgreSQL deployments.

---

## 📚 Table of Contents
- [Feature Overview](#-feature-overview)
- [Architecture Snapshot](#-architecture-snapshot)
- [Getting Started](#-getting-started)
- [Environment Configuration](#-environment-configuration)
- [Running & Development](#-running--development)
- [Testing](#-testing)
- [Deployment & Operations Docs](#-deployment--operations-docs)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Feature Overview

| Area | Highlights |
|------|------------|
| Inventory | Track items, batches, storage locations, and full movement history with CSV import/export and audit-ready logs. |
| Production | Capture daily throughput, customer totals, routing steps, and workstation queues for live shop-floor visibility. |
| Orders & Reservations | Model orders, BOM components, reservations, and consumption events to keep material usage synchronized. |
| Purchasing & Quality | Log purchase requests, RMAs, and quality events with document storage for traceability. |
| Workstations | Surface job queues, work instructions, and printer-ready labels for each station. |
| Reporting | Export comprehensive ZIP bundles (items, locations, batches, movements) and render production charts. |
| Labeling | Manage Zebra printer hosts, ZPL label templates, and process-to-template mappings. |
| Security & Auditing | Role-based access controls, admin session timeouts, access logs, and default superuser bootstrapping. |

---

## 🧱 Architecture Snapshot

| Layer | Technology |
|-------|------------|
| Web Application | Flask with Blueprints, WTForms, and Jinja2 templates |
| Persistence | SQLAlchemy ORM backed by PostgreSQL |
| Task Execution | Synchronous request handling with gunicorn (WSGI) |
| Front-end | Server-rendered UI optimized for dark themes and barcode input |
| Platform | Linux edge hardware (Raspberry Pi, Intel NUC, or similar mini PC) |

Repository layout (trimmed to the most relevant directories):

```
invapp2/
├── app.py                 # Gunicorn entry point
├── config.py              # Configuration defaults & upload paths
├── invapp/
│   ├── __init__.py        # App factory, blueprints, navigation metadata
│   ├── models.py          # SQLAlchemy models for inventory, production, auth, printers
│   ├── routes/            # Feature blueprints: inventory, orders, purchasing, quality, etc.
│   ├── templates/         # HTML templates grouped per feature
│   └── static/            # Compiled assets and uploaded documents
├── requirements.txt       # Python dependencies
└── start_inventory.sh     # Helper script for provisioning and launching gunicorn
```

---

## 🛠 Getting Started

### Prerequisites
- Python 3.10+
- PostgreSQL 13+ with `libpq` client libraries
- `git`, `pip`, `setuptools`, and `wheel`

### Clone & Install
```bash
git clone https://github.com/YOUR-ORG/Hyperion-Operations-Hub.git
cd Hyperion-Operations-Hub/invapp2
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

### Initialize the Database
Configure environment variables (see below), then create the schema:

```bash
export DB_URL="postgresql+psycopg2://USER:PASSWORD@localhost/invdb"
export SECRET_KEY="change_me"
export ADMIN_USER="superuser"
export ADMIN_PASSWORD="change_me"
flask --app app shell <<'PYTHON'
from invapp.extensions import db
from invapp import create_app
app = create_app()
with app.app_context():
    db.create_all()
PYTHON
```

If you are running on Raspberry Pi or Debian-based systems, ensure the `libpq5`
and `libpq-dev` packages are installed before installing `psycopg2`.

---

## ⚙️ Environment Configuration

All configuration values are read from environment variables with sane defaults
defined in [`invapp2/config.py`](invapp2/config.py). Common options include:

| Variable | Purpose | Default |
|----------|---------|---------|
| `DB_URL` | SQLAlchemy connection string for PostgreSQL. | `postgresql+psycopg2://inv:change_me@localhost/invdb` |
| `SECRET_KEY` | Flask session secret. | `supersecret` |
| `ADMIN_USER` / `ADMIN_PASSWORD` | Seed credentials for the superuser created on startup. | `superuser` / `joshbaldus` |
| `ADMIN_SESSION_TIMEOUT` | Idle timeout (seconds) for admin pages. | `300` |
| `ZEBRA_PRINTER_HOST` / `ZEBRA_PRINTER_PORT` | Default Zebra printer host and port. | `localhost` / `9100` |
| `WORK_INSTRUCTION_ALLOWED_EXTENSIONS` | File types accepted for uploaded work instructions. | `{"pdf"}` |
| `ITEM_ATTACHMENT_ALLOWED_EXTENSIONS` | Allowed item attachment file types. | `{"pdf","png","jpg","jpeg"}` |
| `QUALITY_ATTACHMENT_ALLOWED_EXTENSIONS` | Allowed RMA/quality attachment types. | Multiple formats (PDF, Office, images, CSV, TXT). |

Uploaded documents are stored under `invapp2/invapp/static/...` by default; make
sure the service account has write access to these directories in production.

---

## 🏃 Running & Development

Start the production-ready WSGI server with gunicorn:

```bash
gunicorn --bind 0.0.0.0:8000 app:app
```

For local development you can use the Flask development server (not
recommended for production):

```bash
flask --app app run --debug
```

The helper script [`start_inventory.sh`](invapp2/start_inventory.sh) bundles
virtual environment creation, dependency installation, and gunicorn startup with
sensible defaults—ideal for first-time provisioning. For the full operations
console, use [`start_operations_console.sh`](start_operations_console.sh); it
applies the same provisioning steps and launches gunicorn pointed at
`app:app`.

> **Heads-up:** The application now boots even if PostgreSQL is offline. When
> this happens, the home page renders an alert that lists recovery commands
> (checking the PostgreSQL service, starting it, confirming `DB_URL`, and
> rerunning `./start_operations_console.sh`). This allows you to verify that the
> web layer is healthy before tackling database connectivity.

---

## ✅ Testing

Automated tests live under `invapp2/tests`. After activating the virtual
environment, run:

```bash
pytest
```

---

## 📦 Deployment & Operations Docs

Additional, scenario-specific documentation lives under the [`docs/`](docs)
directory:

- [`docs/HARDWARE.md`](docs/HARDWARE.md) — Quick hardware tiers for pilots,
  small teams, and multi-workcell deployments.
- [`docs/hardware-guide.md`](docs/hardware-guide.md) — Deep dive into bill of
  materials, sizing, peripherals, and maintenance practices.
- [`docs/deployment-guide.md`](docs/deployment-guide.md) — Step-by-step
  provisioning guide for Raspberry Pi and Debian/Ubuntu hosts.
- [`docs/database-schema.md`](docs/database-schema.md) — Entity relationship
overview and table reference for inventory, production, and access control.

---

## 🤝 Contributing

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/amazing-improvement`.
3. Commit your changes: `git commit -am "Describe feature"`.
4. Push to the branch: `git push origin feature/amazing-improvement`.
5. Open a pull request and describe the context/test coverage.

Bug reports and feature suggestions are always welcome—please include logs,
steps to reproduce, and environment details when filing an issue.

---

## 📄 License

This project is distributed under the MIT License. See [`LICENSE`](LICENSE) for
full terms.
