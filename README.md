# Hyperion Operations Hub

Hyperion Operations Hub is a Flask-based operations console for manufacturing teams. It unifies inventory, production, purchasing, quality, workstation activity, and KPI reporting behind a single authenticated UI and shared PostgreSQL database. The app is designed for lightweight edge deployments (for example, an industrial mini PC) while still supporting a centralized database. The integrated MDI (Management for Daily Improvement) module provides dashboards and meeting tools within the same app and auth system. For the app entry point and blueprint registration, see [`invapp2/app.py`](invapp2/app.py) and [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py). For the MDI module, see [`invapp2/invapp/mdi`](invapp2/invapp/mdi).

## Table of Contents
1. [Project Overview](#project-overview)
2. [Quick Start](#quick-start)
3. [Architecture & How It’s Organized](#architecture--how-its-organized)
4. [Configuration](#configuration)
5. [Web / UI Layer (Templates, Frontend, Static)](#web--ui-layer-templates-frontend-static)
6. [API / Routes](#api--routes)
7. [Database](#database)
8. [Permissions / Roles](#permissions--roles)
9. [Background Tasks / Schedules](#background-tasks--schedules)
10. [Backups & Restore](#backups--restore)
11. [Logging, Errors, and Debugging](#logging-errors-and-debugging)
12. [Testing](#testing)
13. [Deployment Notes](#deployment-notes)
14. [Network Stability & Self-Healing (Linux Host)](#network-stability--self-healing-linux-host)
15. [Contributing / Development Conventions](#contributing--development-conventions)
16. [How to Make Common Changes](#how-to-make-common-changes)

---

## Project Overview

**What it is:** Hyperion Operations Hub is a Flask web application that runs a manufacturing operations console. It includes inventory control, production tracking, purchasing workflows, quality/RMA handling, workstation tools, administrative tools, and an integrated KPI/MDI dashboard experience in the same application. The main Flask app is created in `create_app()` and registers blueprints for each feature area. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).

**Who it’s for:** Operations, production, purchasing, and quality teams who need a single source of truth for day-to-day manufacturing workflows with built-in dashboards and audit logging.

**Key capabilities (backed by code):**
- **Inventory and stock movement tracking**: items, batches (soft-delete), locations, and movement records live in SQLAlchemy models and are served under the `/inventory` blueprint. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py) and [`invapp2/invapp/routes/inventory.py`](invapp2/invapp/routes/inventory.py).
- **Orders and production records**: order, BOM, routing, and production models are defined in the core models file, and production history routes are registered in the production blueprint. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py) and [`invapp2/invapp/routes/production.py`](invapp2/invapp/routes/production.py).
- **Purchasing and quality workflows**: purchase requests, quality/RMA models, and route handlers are in the core models and the purchasing/quality blueprints. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py), [`invapp2/invapp/routes/purchasing.py`](invapp2/invapp/routes/purchasing.py), and [`invapp2/invapp/routes/quality.py`](invapp2/invapp/routes/quality.py).
- **Workstation tools**: the `work` blueprint serves workstation-specific views and tools. See [`invapp2/invapp/routes/work.py`](invapp2/invapp/routes/work.py).
- **MDI (KPI Board) module**: dashboards, meeting deck, reporting UI, and API endpoints for KPI entries live in the MDI blueprint and its models. See [`invapp2/invapp/mdi`](invapp2/invapp/mdi) and [`invapp2/invapp/mdi/models.py`](invapp2/invapp/mdi/models.py).
- **Automated backups with scheduling**: APScheduler-driven backups run in the Flask process, with storage and reporting in the backup service. See [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).
- **Operations monitor (terminal UI)**: a separate monitor process surfaces health, logs, and backup status, launched by the startup scripts. See [`ops_monitor`](ops_monitor) and [`start_operations_console.sh`](start_operations_console.sh).

---

## Quick Start

### Prerequisites
- **Python 3.10+** (required by the code and dependencies). See [`invapp2/requirements.txt`](invapp2/requirements.txt).
- **PostgreSQL 13+** reachable by the app (the default DB URL expects Postgres). See [`invapp2/config.py`](invapp2/config.py).
- **Gunicorn** (installed via requirements; used by the production scripts). See [`invapp2/requirements.txt`](invapp2/requirements.txt).

### Clone & install
```bash
git clone <YOUR_REPO_URL>
cd Hyperion-Operations-Hub/invapp2
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

### Configure environment
Set required environment variables before running:
```bash
export DB_URL="postgresql+psycopg2://USER:PASSWORD@localhost/invdb"
export SECRET_KEY="change_me"
export ADMIN_USER="superuser"
export ADMIN_PASSWORD="change_me"
```
Defaults and additional settings are in [`invapp2/config.py`](invapp2/config.py).

### Run locally (developer mode)
From `invapp2/`:
```bash
python app.py
```
This uses Flask’s built-in development server and also launches the operations monitor. See [`invapp2/app.py`](invapp2/app.py).

### Run locally (production-like, Gunicorn)
From the repository root:
```bash
./start_operations_console.sh
```
This script creates a venv, installs dependencies, runs a health check, starts the ops monitor, and launches Gunicorn. See [`start_operations_console.sh`](start_operations_console.sh).

### Access the UI
- **UI:** `http://localhost:5000/` (developer mode) or `http://localhost:8000/` (Gunicorn scripts default). Ports are controlled by `PORT`. See [`invapp2/app.py`](invapp2/app.py) and [`start_operations_console.sh`](start_operations_console.sh).
- **Login:** `http://localhost:5000/auth/login` (or `:8000`), using the `ADMIN_USER` / `ADMIN_PASSWORD` you configured. See [`invapp2/invapp/routes/auth.py`](invapp2/invapp/routes/auth.py) and [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).

### Verify it works (checklist)
- [ ] The app boots without a database error banner on the home page. See startup DB checks in [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
- [ ] You can log in with your admin credentials. See [`invapp2/invapp/routes/auth.py`](invapp2/invapp/routes/auth.py).
- [ ] `/mdi/meeting` loads MDI data (it will be seeded on first boot). See [`invapp2/invapp/mdi/routes/meeting.py`](invapp2/invapp/mdi/routes/meeting.py) and seed logic in [`invapp2/invapp/mdi/models.py`](invapp2/invapp/mdi/models.py).

---

## Architecture & How It’s Organized

### High-level architecture (text diagram)
```
[Browser]
   │
   ▼
[Flask App (invapp2/app.py -> invapp.create_app)]
   │   ├─ Blueprints: inventory, orders, purchasing, quality, work, production, reports, admin, users
   │   ├─ MDI Blueprint: /mdi/* dashboards & APIs
   │   ├─ Services: backup scheduler, status bus, stock transfer
   ▼
[PostgreSQL DB]
   │
   └─ Background: APScheduler for backups + ops_monitor (separate process)
```
Blueprint registration and startup flow are in [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py). The ops monitor is a separate process in [`ops_monitor`](ops_monitor).

### Repository structure
```
Hyperion-Operations-Hub/
├── invapp2/                  # Main Flask application package
│   ├── app.py                 # Entry point for dev server
│   ├── config.py              # Runtime configuration defaults
│   ├── requirements.txt       # Python dependencies
│   ├── migrations/            # Alembic migrations
│   ├── scripts/               # Helper CLI wrappers
│   ├── tests/                 # Pytest suite
│   └── invapp/                # Application package (blueprints, models, services)
├── ops_monitor/               # Terminal-based operations monitor
├── docs/                      # Additional documentation and guides
├── support/                   # Diagnostics and log support scripts
├── start_operations_console.sh# Production-like launcher (Gunicorn + monitor)
└── README.md
```
Important directories and what belongs there:
- **`invapp2/invapp/`**: Flask app code (blueprints, models, services, templates, static). This is where most feature work happens. See [`invapp2/invapp`](invapp2/invapp).
- **`invapp2/migrations/`**: Alembic migrations for schema changes. See [`invapp2/migrations`](invapp2/migrations).
- **`invapp2/tests/`**: pytest tests. See [`invapp2/tests`](invapp2/tests).
- **`ops_monitor/`**: the terminal UI monitor launched by startup scripts. See [`ops_monitor`](ops_monitor).
- **`docs/`**: deployment and hardware guidance. See [`docs`](docs).

### Execution flow (startup)
1. **Entry point**: `invapp2/app.py` creates the Flask app and optionally launches the ops monitor when run directly. See [`invapp2/app.py`](invapp2/app.py).
2. **App factory**: `create_app()` loads configuration, sets up logging, initializes Flask-Login and SQLAlchemy, and ensures schema and seed data. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
3. **Blueprint registration**: All feature routes are registered, including the MDI blueprint. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py) and [`invapp2/invapp/mdi/__init__.py`](invapp2/invapp/mdi/__init__.py).
4. **Backup scheduler**: APScheduler jobs are started in non-test environments. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py) and [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).

---

## Configuration

### Environment variables (runtime)
These are pulled directly from environment variables or startup scripts:

| Variable | Purpose | Default / Example | Source |
| --- | --- | --- | --- |
| `DB_URL` | SQLAlchemy database URL (PostgreSQL). | `postgresql+psycopg2://inv:change_me@localhost/invdb` | [`invapp2/config.py`](invapp2/config.py) |
| `SECRET_KEY` | Flask session secret. | `supersecret` | [`invapp2/config.py`](invapp2/config.py) |
| `ADMIN_USER` | Bootstrapped admin username. | `superuser` | [`invapp2/config.py`](invapp2/config.py) |
| `ADMIN_PASSWORD` | Bootstrapped admin password. | `joshbaldus` (change it!) | [`invapp2/config.py`](invapp2/config.py) |
| `ADMIN_SESSION_TIMEOUT` | Session timeout in seconds. | `300` | [`invapp2/config.py`](invapp2/config.py) |
| `BACKUP_DIR` | Preferred backup directory used by the backup service. | (none) | [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py) |
| `BACKUP_DIR_AUTO` | Directory for auto-imported backups. | (none) | [`invapp2/config.py`](invapp2/config.py) |
| `MDI_DEFAULT_RECIPIENTS` | Default recipient list for MDI emails. | empty | [`invapp2/config.py`](invapp2/config.py) |
| `MDI_DEFAULT_SENDER` | Default sender (empty so mail client can choose). | empty | [`invapp2/config.py`](invapp2/config.py) |
| `FRAMING_PANEL_OFFSET` | Offset for framing panel UI. | `0` | [`invapp2/config.py`](invapp2/config.py) |
| `PURCHASING_ATTACHMENT_UPLOAD_FOLDER` | Override attachment upload directory. | `<repo>/invapp2/invapp/static/purchase_request_attachments` | [`invapp2/config.py`](invapp2/config.py) |
| `PURCHASING_ATTACHMENT_MAX_SIZE_MB` | Max attachment size (MB). | `25` | [`invapp2/config.py`](invapp2/config.py) |
| `INVENTORY_REMOVE_REASONS` | CSV list of allowed inventory removal reasons. | `Damage,Expired,...` | [`invapp2/config.py`](invapp2/config.py) |
| `ZEBRA_PRINTER_HOST` | Zebra printer host. | `localhost` | [`invapp2/config.py`](invapp2/config.py) |
| `ZEBRA_PRINTER_PORT` | Zebra printer port. | `9100` | [`invapp2/config.py`](invapp2/config.py) |
| `HOST` | Gunicorn bind host. | `0.0.0.0` | [`start_operations_console.sh`](start_operations_console.sh) |
| `PORT` | App port (dev server + Gunicorn + monitor). | `5000` (dev) / `8000` (scripts) | [`invapp2/app.py`](invapp2/app.py), [`start_operations_console.sh`](start_operations_console.sh) |
| `GUNICORN_WORKERS` | Gunicorn worker count. | `2` | [`start_operations_console.sh`](start_operations_console.sh) |
| `GUNICORN_TIMEOUT` | Gunicorn worker timeout seconds. | `600` | [`start_operations_console.sh`](start_operations_console.sh) |
| `ENABLE_OPS_MONITOR` | Enable the terminal ops monitor. | `1` | [`start_operations_console.sh`](start_operations_console.sh), [`ops_monitor/launcher.py`](ops_monitor/launcher.py) |
| `OPS_MONITOR_DB_URL` | DB URL to show in ops monitor (masked). | falls back to `DB_URL` | [`ops_monitor/monitor.py`](ops_monitor/monitor.py) |
| `OPS_MONITOR_LAUNCH_MODE` | Monitor launch mode (`window`, `background`, `headless`). | `window` | [`ops_monitor/launcher.py`](ops_monitor/launcher.py) |
| `OPS_MONITOR_TERMINAL` | Force a specific terminal app. | (none) | [`ops_monitor/launcher.py`](ops_monitor/launcher.py) |
| `OPS_MONITOR_REFRESH_INTERVAL` | Terminal refresh interval (seconds). | `0.5` | [`ops_monitor/monitor.py`](ops_monitor/monitor.py) |
| `OPS_MONITOR_LOG_MAX_LINES` | Max log lines kept for scrollback. | `200` | [`ops_monitor/monitor.py`](ops_monitor/monitor.py) |
| `OPS_MONITOR_LOG_WINDOW` | Visible log window height (lines). | `18` | [`ops_monitor/monitor.py`](ops_monitor/monitor.py) |
| `OPS_MONITOR_DEBUG` | Log terminal key events for diagnostics. | `0` | [`ops_monitor/monitor.py`](ops_monitor/monitor.py) |
| `PYTHON` | Python executable used for the ops monitor. | current interpreter | [`ops_monitor/launcher.py`](ops_monitor/launcher.py) |
| `APP_DIR`, `VENV_DIR`, `REQUIREMENTS_FILE`, `APP_MODULE`, `MONITOR_LOG_FILE` | Startup script overrides for the launcher. | (script defaults) | [`start_operations_console.sh`](start_operations_console.sh) |
| `HEALTHCHECK_FATAL`, `HEALTHCHECK_DRY_RUN` | Startup healthcheck behavior. | `0` | [`start_operations_console.sh`](start_operations_console.sh), [`invapp2/invapp/healthcheck.py`](invapp2/invapp/healthcheck.py) |

### Config files and loading
- **`invapp2/config.py`**: `Config` is loaded in `create_app()` via `app.config.from_object(Config)`. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py) and [`invapp2/config.py`](invapp2/config.py).
- **Alembic config**: `alembic.ini` points to the migrations folder and defaults to SQLite unless overridden by `DB_URL`. See [`invapp2/alembic.ini`](invapp2/alembic.ini) and [`invapp2/migrations/env.py`](invapp2/migrations/env.py).

### Dev vs prod differences
- **Dev**: `python invapp2/app.py` runs the Flask dev server on port 5000 and launches the ops monitor in a separate process. See [`invapp2/app.py`](invapp2/app.py).
- **Prod-like**: `./start_operations_console.sh` runs a health check and launches Gunicorn on port 8000 by default. See [`start_operations_console.sh`](start_operations_console.sh).

---

## Web / UI Layer (Templates, Frontend, Static)

### Templates
- Templates live in `invapp2/invapp/templates/` and are organized by feature: `inventory/`, `orders/`, `purchasing/`, `quality/`, `production/`, `work/`, `mdi/`, etc. See [`invapp2/invapp/templates`](invapp2/invapp/templates).
- The base layout is `invapp2/invapp/templates/base.html`, which other templates extend. See [`invapp2/invapp/templates/base.html`](invapp2/invapp/templates/base.html).
- MDI templates are under `invapp2/invapp/templates/mdi/` and are registered via the MDI blueprint. See [`invapp2/invapp/mdi/__init__.py`](invapp2/invapp/mdi/__init__.py) and [`invapp2/invapp/templates/mdi`](invapp2/invapp/templates/mdi).

### Static assets
- Static files live in `invapp2/invapp/static/` (global) and `invapp2/invapp/static/mdi/` (MDI-specific CSS/JS). See [`invapp2/invapp/static`](invapp2/invapp/static) and [`invapp2/invapp/static/mdi`](invapp2/invapp/static/mdi).
- Uploaded files (work instructions, item attachments, purchasing attachments, quality attachments) are configured to be stored under `invapp2/invapp/static/` subfolders by default. See [`invapp2/config.py`](invapp2/config.py).

### How to add/edit a page safely
1. **Add a route** in the relevant blueprint (e.g., `invapp2/invapp/routes/inventory.py` for inventory pages). See blueprint usage in [`invapp2/invapp/routes/inventory.py`](invapp2/invapp/routes/inventory.py).
2. **Create a template** under the matching folder in `invapp2/invapp/templates/` and extend `base.html`. See [`invapp2/invapp/templates/base.html`](invapp2/invapp/templates/base.html).
3. **Protect the page** with permission checks using `blueprint_page_guard` or `ensure_page_access`. See [`invapp2/invapp/auth.py`](invapp2/invapp/auth.py) and [`invapp2/invapp/permissions.py`](invapp2/invapp/permissions.py).

### Forms and validation
This app uses standard Flask form handling with `request.form` and manual validation in route handlers (no WTForms). For examples, see purchasing and MDI report handlers in [`invapp2/invapp/routes/purchasing.py`](invapp2/invapp/routes/purchasing.py) and [`invapp2/invapp/mdi/routes/reports.py`](invapp2/invapp/mdi/routes/reports.py).

---

## API / Routes

### Route organization
- **Blueprints** are organized per feature and registered in `create_app()`. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
- Example blueprint files:
  - Inventory: [`invapp2/invapp/routes/inventory.py`](invapp2/invapp/routes/inventory.py)
  - Orders: [`invapp2/invapp/routes/orders.py`](invapp2/invapp/routes/orders.py)
  - Purchasing: [`invapp2/invapp/routes/purchasing.py`](invapp2/invapp/routes/purchasing.py)
  - Quality: [`invapp2/invapp/routes/quality.py`](invapp2/invapp/routes/quality.py)
  - Workstations: [`invapp2/invapp/routes/work.py`](invapp2/invapp/routes/work.py)
  - Production: [`invapp2/invapp/routes/production.py`](invapp2/invapp/routes/production.py)
  - Reports: [`invapp2/invapp/routes/reports.py`](invapp2/invapp/routes/reports.py)
  - Admin: [`invapp2/invapp/routes/admin.py`](invapp2/invapp/routes/admin.py)
  - Users: [`invapp2/invapp/routes/users.py`](invapp2/invapp/routes/users.py)
  - MDI: [`invapp2/invapp/mdi/routes`](invapp2/invapp/mdi/routes)

### Key endpoints (examples)
> **Note:** Most routes are HTML views; API endpoints are JSON and typically live under `/api` or `/mdi/api`.

- **Login page**: `GET /auth/login` → renders login template. See [`invapp2/invapp/routes/auth.py`](invapp2/invapp/routes/auth.py).
- **Home dashboard**: `GET /` → renders home dashboard with summaries. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
- **Inventory dashboard**: `GET /inventory/` → inventory home page. See [`invapp2/invapp/routes/inventory.py`](invapp2/invapp/routes/inventory.py).
- **MDI meeting deck**: `GET /mdi/meeting` → meeting dashboard. See [`invapp2/invapp/mdi/routes/meeting.py`](invapp2/invapp/mdi/routes/meeting.py).

#### JSON APIs
- **Item search**: `GET /api/items/search?q=<query>` returns a list of matching items (requires purchasing access). Example response:
  ```json
  [
    {
      "id": 123,
      "item_number": "ABC-123",
      "name": "Widget",
      "on_hand_total": 42,
      "locations": [
        {"code": "A1", "description": "Shelf A1", "quantity": 40}
      ]
    }
  ]
  ```
  See [`invapp2/invapp/routes/item_search.py`](invapp2/invapp/routes/item_search.py).

- **Item stock**: `GET /api/items/<item_id>/stock` returns totals + location breakdown. See [`invapp2/invapp/routes/item_search.py`](invapp2/invapp/routes/item_search.py).

- **MDI entries API**: `GET /mdi/api/mdi_entries?category=Safety&status=Open&date=YYYY-MM-DD` returns filtered KPI entries. See [`invapp2/invapp/mdi/routes/api.py`](invapp2/invapp/mdi/routes/api.py).

- **MDI create entry**: `POST /mdi/api/mdi_entries` with JSON payload creates a new entry. See [`invapp2/invapp/mdi/routes/api.py`](invapp2/invapp/mdi/routes/api.py).

Auth requirements are enforced by blueprint guards and permission checks in each route. See [`invapp2/invapp/auth.py`](invapp2/invapp/auth.py) and [`invapp2/invapp/permissions.py`](invapp2/invapp/permissions.py).

---

## Database

### Engine and connection
- Uses **PostgreSQL** via SQLAlchemy. Connection string is `DB_URL` / `SQLALCHEMY_DATABASE_URI`. See [`invapp2/config.py`](invapp2/config.py).
- SQLAlchemy is initialized in the app factory. See [`invapp2/invapp/extensions.py`](invapp2/invapp/extensions.py) and [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).

### Models / schema overview
Core entities are defined in `invapp2/invapp/models.py`:
- **Users / Roles / Permissions**: `User`, `Role`, `PageAccessRule`. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py).
- **Inventory**: `Item`, `Batch` (soft delete), `Location`, `Movement`, `Reservation`. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py).
- **Orders**: `Order`, `OrderLine`, `OrderComponent`, `BillOfMaterial`, `RoutingStep`. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py).
- **Purchasing**: `PurchaseRequest`, `PurchaseRequestAttachment`. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py).
- **Quality**: `RMARequest`, `RMAAttachment`, `RMAStatusEvent`. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py).
- **Production**: `ProductionDailyRecord`, `ProductionCustomer`, `ProductionOutputFormula`. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py).
- **Admin/Logging**: `AccessLog`, `AdminAuditLog`, `OpsEventLog`, `BackupRun`. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py).

MDI entities are defined in `invapp2/invapp/mdi/models.py`:
- `MDIEntry` and `CategoryMetric` power dashboards and APIs. See [`invapp2/invapp/mdi/models.py`](invapp2/invapp/mdi/models.py).

### Migrations
This repo includes Alembic migrations in `invapp2/migrations/`:
```bash
cd invapp2
export DB_URL="postgresql+psycopg2://USER:PASSWORD@localhost/invdb"
alembic -c alembic.ini upgrade head
```
- Create a new migration:
  ```bash
  alembic -c alembic.ini revision --autogenerate -m "add_new_field"
  ```
- Roll back one migration:
  ```bash
  alembic -c alembic.ini downgrade -1
  ```
Alembic reads `DB_URL` at runtime. See [`invapp2/migrations/env.py`](invapp2/migrations/env.py).

### Startup schema/seed behavior
`create_app()` will `db.create_all()`, apply legacy schema backfills, ensure MDI tables, seed MDI demo data, and create a default admin user/roles. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py) and [`invapp2/invapp/mdi/models.py`](invapp2/invapp/mdi/models.py).

### Common DB tasks
- **Health check**:
  ```bash
  python -m invapp.healthcheck --fatal
  ```
  See [`invapp2/invapp/healthcheck.py`](invapp2/invapp/healthcheck.py).
- **Primary key sequence check/repair**:
  ```bash
  python -m invapp.db_sanity_check
  python -m invapp.db_sanity_check --fix
  ```
  See [`invapp2/invapp/db_sanity_check.py`](invapp2/invapp/db_sanity_check.py).
- **Batch soft-delete check**:
  ```bash
  python -m invapp.batch_soft_delete_check
  ```
  See [`invapp2/invapp/batch_soft_delete_check.py`](invapp2/invapp/batch_soft_delete_check.py).

### Data integrity / constraints to know
- **Batch soft deletes**: `Batch` uses `removed_at` with a custom query class to hide removed records by default. See [`invapp2/invapp/models.py`](invapp2/invapp/models.py).
- **Sequence repair**: primary key sequences are repaired during startup and via CLI tooling to recover from manual data imports. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py) and [`invapp2/invapp/db_sanity_check.py`](invapp2/invapp/db_sanity_check.py).

---

## Permissions / Roles

### Roles
- Core roles are created on startup (`admin`, `viewer`, `editor`, `purchasing`, `quality`, `public`). See `_ensure_core_roles()` in [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
- A legacy `user` role is created if a user is saved with no explicit roles. See [`invapp2/invapp/routes/users.py`](invapp2/invapp/routes/users.py).

### Page permissions
- Default page access rules live in `DEFAULT_PAGE_ACCESS` and can be overridden in the database. See [`invapp2/invapp/permissions.py`](invapp2/invapp/permissions.py).
- Guards and permission checks are used in routes via `blueprint_page_guard` and `ensure_page_access`. See [`invapp2/invapp/auth.py`](invapp2/invapp/auth.py) and [`invapp2/invapp/permissions.py`](invapp2/invapp/permissions.py).

### Emergency/offline access
If the database is offline at startup, the app uses an `OfflineAdminUser` that grants temporary access so operators can troubleshoot. See [`invapp2/invapp/offline.py`](invapp2/invapp/offline.py).

---

## Background Tasks / Schedules

- **Automated backups** use APScheduler inside the Flask process. Jobs run on a configurable interval and log to the backup directory. See [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).
- The scheduler is started in `create_app()` when not in testing mode. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
- **Ops monitor** is a separate Python process launched by the startup scripts (not a Flask background task). See [`ops_monitor`](ops_monitor) and [`start_operations_console.sh`](start_operations_console.sh).

To disable automated backups in dev, set `BACKUP_SCHEDULER_ENABLED` to `False` in config overrides when calling `create_app()` (it defaults to `True`). See scheduler checks in [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).

---

## Backups & Restore

### How backups are created
- **Scheduler**: the Flask process runs APScheduler jobs that perform database backups on a configurable interval. The interval lives in the `app_setting` key `backup_frequency_hours` (default 4 hours). See the scheduler and settings logic in [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py) and scheduler startup in [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
- **Artifacts**: database backups are plain `.sql` files created by `pg_dump`, and critical file attachments are archived alongside them. See the backup creation helpers in [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).

### Where backups are stored
- **Primary directory**: `BACKUP_DIR` (env or config). If not writable, the service falls back to `<instance>/backups` and then `./backups`. See [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).
- **Subfolders**:
  - `db/` for database `.sql` backups
  - `files/` for attachment archives
  - `tmp/` for staging work during restore

### How restore works now (no root required)
- **Superuser-only**: only the configured application superuser can access restore controls or execute a restore. See [`invapp2/invapp/superuser.py`](invapp2/invapp/superuser.py) and the admin restore route in [`invapp2/invapp/routes/admin.py`](invapp2/invapp/routes/admin.py).
- **No OS root required**: restore uses `psql` with the `DB_URL` credentials; it does *not* require sudo/root or service restarts. See restore execution in [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).
- **Safe flow**:
  1. The user must type `RESTORE <filename>` and `I UNDERSTAND THIS WILL OVERWRITE DATA` to confirm. See the restore form and validation in [`invapp2/invapp/templates/admin/backups.html`](invapp2/invapp/templates/admin/backups.html) and [`invapp2/invapp/routes/admin.py`](invapp2/invapp/routes/admin.py).
  2. The app enters maintenance mode during the restore to prevent other requests. See the restore guard in [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
  3. Active DB sessions are terminated (best-effort), the `public` schema is dropped/recreated, then the `.sql` is applied via `psql`. See restore steps in [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).
  4. Sequence repair runs after a successful restore to re-sync primary key sequences. See the post-restore step in [`invapp2/invapp/routes/admin.py`](invapp2/invapp/routes/admin.py).

### Required environment variables
- `DB_URL`: SQLAlchemy/PostgreSQL URL used by `pg_dump` and `psql`.
- `BACKUP_DIR`: base folder for backup storage (optional, but recommended).
- `BACKUP_DIR_AUTO`: directory for auto-imported backups (optional).
See configuration defaults in [`invapp2/config.py`](invapp2/config.py) and backup handling in [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).

### Permissions model (restore)
- **Restore**: superuser-only (the configured `ADMIN_USER` account).
- **Backup listing/download**: admin or superuser per the admin routes.
See access enforcement in [`invapp2/invapp/superuser.py`](invapp2/invapp/superuser.py) and [`invapp2/invapp/routes/admin.py`](invapp2/invapp/routes/admin.py).

### Viewing restore/backup status (ops monitor)
- The popout status terminal shows **backup status** in the “Backup Status” panel and recent **restore events** in the Events panel.
- Restore progress events (start/reset/apply/complete) and sanitized stdout/stderr are published to the status bus (`ops_event_log`), so they appear in the terminal UI. See [`invapp2/invapp/services/status_bus.py`](invapp2/invapp/services/status_bus.py), [`ops_monitor/metrics.py`](ops_monitor/metrics.py), and [`ops_monitor/monitor.py`](ops_monitor/monitor.py).

### Troubleshooting restore failures
- **`DB_URL` incorrect**: `psql` will fail to connect. Verify `DB_URL` and that the database exists. See config defaults in [`invapp2/config.py`](invapp2/config.py).
- **`psql` not installed**: install PostgreSQL client tools on the host (`psql` must be on `PATH`).
- **Permission errors**: ensure the database user has rights to drop/create schema and restore objects.
- **Locked sessions**: if active sessions block schema drops, ensure the DB user can terminate connections or stop the app briefly before retrying.

---

## Logging, Errors, and Debugging

- **App logs**: `create_app()` configures a rotating log file (default `support/operations.log`) and also sends warnings to the status bus. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
- **Backup logs**: stored in `backup.log` within the backup directory. See [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).
- **Status bus**: logs events to memory and the `ops_event_log` table for the ops monitor. See [`invapp2/invapp/services/status_bus.py`](invapp2/invapp/services/status_bus.py).
- **Diagnostics script**: `support/run_diagnostics.sh` collects system status for troubleshooting. See [`support/run_diagnostics.sh`](support/run_diagnostics.sh).

Common debugging steps:
- Run the health check before startup: `python -m invapp.healthcheck --fatal`. See [`invapp2/invapp/healthcheck.py`](invapp2/invapp/healthcheck.py).
- Watch `support/operations.log` and the ops monitor UI for database errors and backup status. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py) and [`ops_monitor/monitor.py`](ops_monitor/monitor.py).

---

## Testing

- Tests are in `invapp2/tests/` and use `pytest`. See [`invapp2/tests`](invapp2/tests) and [`invapp2/requirements.txt`](invapp2/requirements.txt).
- Run the full suite:
  ```bash
  cd invapp2
  pytest
  ```

To add a test:
1. Create a new `test_*.py` file in `invapp2/tests/`.
2. Use existing tests as references for expected fixtures and patterns. See [`invapp2/tests`](invapp2/tests).

---

## Deployment Notes

- **Recommended launch path**: use `./start_operations_console.sh` from the repo root. It creates a venv, installs deps, runs a health check, and launches Gunicorn. See [`start_operations_console.sh`](start_operations_console.sh).
- **Gunicorn settings**: `HOST`, `PORT`, `GUNICORN_WORKERS`, and `GUNICORN_TIMEOUT` control the server configuration. See [`start_operations_console.sh`](start_operations_console.sh).
- **Reverse proxy**: not configured in this repo; if you deploy behind Nginx/Apache, proxy to the Gunicorn bind address.
- **Backups**: the backup scheduler uses `BACKUP_DIR` or falls back to `instance/backups` or `./backups`. See [`invapp2/invapp/services/backup_service.py`](invapp2/invapp/services/backup_service.py).

---

## Network Stability & Internet Watchdog

Field deployments can look “connected” while DNS or routing has degraded overnight. The Internet Watchdog is a host-level systemd service that continuously checks true reachability and performs self-healing actions when connectivity drops.

### What it does
- **Runs continuously as a systemd service** so it survives reboots and long runtimes.
- **Writes a single-line status file** that the Ops Monitor terminal display reads for its network row.
- **Logs outage/recovery events** for troubleshooting.
- **Attempts recovery actions** (systemd-resolved + NetworkManager restarts, nmcli toggles) when connectivity is lost.

### Components & paths
- **Watchdog script (source)**: `support/internet_watchdog.sh`
- **Installed script**: `/usr/local/bin/internet_watchdog.sh`
- **systemd unit (source)**: `support/systemd/internet-watchdog.service`
- **Installed unit**: `/etc/systemd/system/internet-watchdog.service`
- **Status file**: `/var/lib/hyperion/network_status.txt`
- **Log file**: `/var/log/internet_watchdog.log`

### Install / update the watchdog (Ubuntu)
Manual install (recommended for first-time setup):
```bash
sudo install -m 0755 support/internet_watchdog.sh /usr/local/bin/internet_watchdog.sh
sudo install -m 0644 support/systemd/internet-watchdog.service /etc/systemd/system/internet-watchdog.service
sudo install -d -m 0755 /var/lib/hyperion
sudo systemctl daemon-reload
sudo systemctl enable --now internet-watchdog.service
```

Optional: refresh watchdog files via the startup helper (idempotent):
```bash
INSTALL_WATCHDOG=1 sudo ./start_operations_console.sh
```

### Configure check host & interval
Defaults are `CHECK_HOST=1.1.1.1` and `INTERVAL=10` (seconds). You can override them with a systemd drop-in:
```bash
sudo systemctl edit internet-watchdog.service
```
Add:
```
[Service]
Environment=CHECK_HOST=8.8.8.8
Environment=INTERVAL=5
```
Then reload:
```bash
sudo systemctl daemon-reload
sudo systemctl restart internet-watchdog.service
```

### Troubleshooting
- **Service status**: `sudo systemctl status internet-watchdog.service`
- **Recent logs**: `sudo journalctl -u internet-watchdog.service -n 200 --no-pager`
- **Watchdog log file**: `sudo tail -f /var/log/internet_watchdog.log`
- **Status file**: `cat /var/lib/hyperion/network_status.txt`

### Verification checklist
- **Simulate offline**: unplug the ethernet cable *or* block ping temporarily:
  ```bash
  sudo iptables -I OUTPUT -p icmp --icmp-type echo-request -j DROP
  ```
  Watch `/var/lib/hyperion/network_status.txt` for `OFFLINE` and confirm the red warning in the Ops Monitor terminal display.
- **Restore connectivity**:
  ```bash
  sudo iptables -D OUTPUT -p icmp --icmp-type echo-request -j DROP
  ```
  Confirm the status line returns to `ONLINE` and the terminal display turns green.
- **Confirm service after reboot**: `systemctl is-enabled internet-watchdog.service` and `systemctl status internet-watchdog.service`.

### Optional: host hardening helpers
The `support/network_stability.sh` script still applies additional host protections (Ethernet power-saving disablement, DNS hardening, NetworkManager restart policy). Run it once if needed:
```bash
sudo support/network_stability.sh
```

### Server/System Terminal Monitor
The Ops Monitor terminal display exists for host-level health visibility even when the web UI is unreachable (for example, during a network outage). It runs alongside the web server and provides live status, logs, and interactive controls.

**How it runs**
- `start_operations_console.sh` launches the ops monitor by default (`ENABLE_OPS_MONITOR=1`). See [`start_operations_console.sh`](start_operations_console.sh).
- The monitor implementation is `ops_monitor/monitor.py`, launched via `ops_monitor/launcher.py`.
- A diagnostic log is written to `/var/log/hyperion/terminal_monitor.log` (fallbacks to `support/hyperion_terminal_monitor.log` if permissions prevent writing to `/var/log`).
- You can run the monitor headless (no TTY required) with `OPS_MONITOR_LAUNCH_MODE=headless` or `./start_operations_console.sh --monitor-headless`.

**Manual run**
```bash
python -m ops_monitor.monitor --target-pid <PID> --app-port 8000 --service-name "Hyperion Operations Hub"
```

**Doctor mode**
```bash
python -m ops_monitor.monitor --doctor
```

**Key bindings (cheat sheet)**
- Navigation: `Tab` / `Shift+Tab`, arrow keys, or `j/k`
- Select/activate: `Enter`
- Quit/back: `q` or `Esc`
- Log panel: `PageUp/PageDown`, `Home/End`, `f` to toggle follow mode

**Refresh & layout tuning**
- Set `OPS_MONITOR_REFRESH_INTERVAL` for update cadence (seconds).
- Set `OPS_MONITOR_LOG_MAX_LINES` and `OPS_MONITOR_LOG_WINDOW` to adjust scrollback and viewport size.

**Systemd (headless)**
- Unit template: `deployment/systemd/hyperion-terminal-monitor.service`
- Install (update paths to match your host):
  ```bash
  sudo install -m 0644 deployment/systemd/hyperion-terminal-monitor.service /etc/systemd/system/hyperion-terminal-monitor.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now hyperion-terminal-monitor.service
  ```

### Network Status Display (Server Terminal)
Network status is intentionally **not** shown in the web UI; if connectivity is down, the site may be unreachable. Instead, the Ops Monitor terminal display reads the watchdog status file and renders it directly on the server console.

**Where the status comes from**
- Watchdog output: `/var/lib/hyperion/network_status.txt`
- Ops Monitor display logic: `ops_monitor/monitor.py` reads the file and highlights OFFLINE lines in red.

**What to look for**
- In the Ops Monitor terminal window, find the **Network** row in the System Health metrics table.
- If the line begins with `OFFLINE`, it is shown in red with a loud prefix (`!!!`) to draw attention.
- If the file is missing or empty, the display shows `UNKNOWN | network watchdog not running`.

**Quick verification**
- Read the file directly:
  ```bash
  cat /var/lib/hyperion/network_status.txt
  ```
- Simulate offline:
  ```bash
  sudo iptables -I OUTPUT -p icmp --icmp-type echo-request -j DROP
  ```
  Confirm the status line flips to `OFFLINE` and the terminal display turns red.
- Restore:
  ```bash
  sudo iptables -D OUTPUT -p icmp --icmp-type echo-request -j DROP
  ```

**Troubleshooting**
- **Network status not showing**:
  - Confirm the watchdog service is running: `systemctl status internet-watchdog.service`.
  - Check file permissions: `ls -l /var/lib/hyperion/network_status.txt`.
  - Verify the log: `sudo tail -f /var/log/internet_watchdog.log`.
- **Monitor opens then closes**:
  - Check `/var/log/hyperion/terminal_monitor.log` for a traceback.
  - Confirm you launched it in a real TTY (or use `--monitor-headless`).
  - Confirm `TERM` is valid (non-empty and not `dumb`).
  - Run `python -m ops_monitor.monitor --doctor`.
- **Controls not responding**:
  - Confirm the terminal has focus (click inside the terminal window).
  - If running inside tmux/screen, check that keybindings are not overridden.
  - Set `OPS_MONITOR_DEBUG=1` and inspect `/var/log/hyperion/terminal_monitor.log`.

---

## Contributing / Development Conventions

- **Framework**: Flask + SQLAlchemy. See [`invapp2/requirements.txt`](invapp2/requirements.txt).
- **Blueprints**: Feature-specific routes live in `invapp2/invapp/routes/` and are registered in `create_app()`. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
- **Templates**: Jinja templates per feature in `invapp2/invapp/templates/`. See [`invapp2/invapp/templates`](invapp2/invapp/templates).
- **Static assets**: stored in `invapp2/invapp/static/`. See [`invapp2/invapp/static`](invapp2/invapp/static).

Additional documentation:
- [Deployment guide](docs/deployment-guide.md)
- [Database schema](docs/database-schema.md)
- [Workstations guide](docs/workstations.md)
- [MDI dashboard](docs/mdi-dashboard.md)

---

## How to Make Common Changes

### Add a new page
1. Choose the appropriate blueprint (or create a new one) under `invapp2/invapp/routes/`.
2. Add a new route function that renders a template. Example blueprint structure: [`invapp2/invapp/routes/inventory.py`](invapp2/invapp/routes/inventory.py).
3. Create a new template under `invapp2/invapp/templates/<feature>/` and extend `base.html`. See [`invapp2/invapp/templates/base.html`](invapp2/invapp/templates/base.html).
4. Add permission guards using `blueprint_page_guard` or `ensure_page_access`. See [`invapp2/invapp/auth.py`](invapp2/invapp/auth.py) and [`invapp2/invapp/permissions.py`](invapp2/invapp/permissions.py).

### Inventory Locations filters & sorting (Row + Description)
Use this when adjusting how the Inventory **Locations** page parses codes, filters by row/description, or changes the sort order.

**Where the page lives**
- **Route:** `GET /inventory/locations` in [`invapp2/invapp/routes/inventory.py`](invapp2/invapp/routes/inventory.py).
- **Template:** [`invapp2/invapp/templates/inventory/list_locations.html`](invapp2/invapp/templates/inventory/list_locations.html).
- **Parser helper:** [`invapp2/invapp/utils/location_parser.py`](invapp2/invapp/utils/location_parser.py).
- **Computed model properties:** `Location.level`, `Location.row`, `Location.bay` in [`invapp2/invapp/models.py`](invapp2/invapp/models.py).

**Parsing rules (Level-Row-Bay)**
- Expected format: `Level-Row-Bay` (e.g., `1-A-1`, `01-A-12`, `2-B-03`).
- Whitespace is trimmed and row is uppercased.
- If the code does **not** match the pattern, `level`, `row`, and `bay` are `None`.
- Examples:
  - `1-A-1` ➜ level `1`, row `A`, bay `1`
  - `01-a-12` ➜ level `1`, row `A`, bay `12`
  - ` 2 - B - 03 ` ➜ level `2`, row `B`, bay `3`
  - `A-1` or `1A1` ➜ all `None`

**Query params and behavior**
- `row`: exact row match (`A`, `B`, etc.). Values are normalized to uppercase.
- `q`: description filter (case-insensitive substring match).
- `sort`: `code` (default), `row`, `description`, `level`, or `bay`.
- `dir`: `asc` or `desc`.
- `page` and `size`: pagination controls (existing behavior).

Examples:
```
/inventory/locations?row=A&q=rack&sort=row&dir=asc
/inventory/locations?sort=description&dir=desc
```

**Sorting details**
- **Row sort:** Row alphabetically, then level numeric, then bay numeric (stable tiebreaker).
- **Code sort:** Natural order using parsed values (so `1-A-2` comes before `1-A-10`).
- **Description sort:** Case-insensitive, with code order as the secondary tiebreaker.

**Filters UI**
- Row dropdown is populated from distinct parsed rows in current `Location.code` values.
- Description filter is a text input that updates the `q` query parameter.
- Clear Filters removes row/description/sort params.

Screenshot placeholder (replace with a real screenshot when available):
![Locations filters UI](docs/screenshots/locations-filters.png)

**Edge cases / limitations**
- Non-standard codes (no `Level-Row-Bay` pattern) keep `level/row/bay` as `None` and sort after valid codes.
- Row filtering only matches parsed rows; invalid codes are excluded when a row filter is active.
- Filtering happens in SQL where possible (description), while row parsing/sorting relies on application logic.

### Add a new DB field + migration
1. Update the SQLAlchemy model in `invapp2/invapp/models.py` (or `invapp2/invapp/mdi/models.py` for MDI tables).
2. Generate an Alembic migration:
   ```bash
   cd invapp2
   alembic -c alembic.ini revision --autogenerate -m "add_field"
   ```
3. Apply the migration:
   ```bash
   alembic -c alembic.ini upgrade head
   ```
4. If the field is required by legacy schema checks, update `_ensure_*_schema` in `invapp2/invapp/__init__.py` accordingly. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).

### Add a new API endpoint
1. Add a new route under the appropriate blueprint (e.g., `/api/...` in `invapp2/invapp/routes/item_search.py`). See [`invapp2/invapp/routes/item_search.py`](invapp2/invapp/routes/item_search.py).
2. Return JSON via `jsonify` and enforce permissions using `blueprint_page_guard` or role checks. See [`invapp2/invapp/auth.py`](invapp2/invapp/auth.py) and [`invapp2/invapp/permissions.py`](invapp2/invapp/permissions.py).
3. Add tests in `invapp2/tests/`.

### Add a new navigation item
1. Add the page to `NAVIGATION_PAGES` in `invapp2/invapp/__init__.py`. See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py).
2. Ensure there is a corresponding permission rule in `DEFAULT_PAGE_ACCESS`. See [`invapp2/invapp/permissions.py`](invapp2/invapp/permissions.py).

### Add a new user role restriction
1. Add (or ensure) the role exists in the database (see role creation in `invapp2/invapp/__init__.py` and `invapp2/invapp/routes/users.py`). See [`invapp2/invapp/__init__.py`](invapp2/invapp/__init__.py) and [`invapp2/invapp/routes/users.py`](invapp2/invapp/routes/users.py).
2. Update `DEFAULT_PAGE_ACCESS` with the new role where appropriate. See [`invapp2/invapp/permissions.py`](invapp2/invapp/permissions.py).
3. Use permission guards in route handlers to enforce access. See [`invapp2/invapp/permissions.py`](invapp2/invapp/permissions.py).

### Add a new MDI category dashboard
1. Update category definitions in `invapp2/invapp/mdi/models.py` and `invapp2/invapp/mdi/routes/dashboard.py` (category lists, colors, and metric config). See [`invapp2/invapp/mdi/models.py`](invapp2/invapp/mdi/models.py) and [`invapp2/invapp/mdi/routes/dashboard.py`](invapp2/invapp/mdi/routes/dashboard.py).
2. Add a new template in `invapp2/invapp/templates/mdi/` for the dashboard view and update routing in `invapp2/invapp/mdi/routes/dashboard.py`. See [`invapp2/invapp/templates/mdi`](invapp2/invapp/templates/mdi) and [`invapp2/invapp/mdi/routes/dashboard.py`](invapp2/invapp/mdi/routes/dashboard.py).

---

## Open Questions

None at this time. All documentation above is based on current repository code.
