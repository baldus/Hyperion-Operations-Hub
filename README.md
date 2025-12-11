# Hyperion Operations Hub

Hyperion Operations Hub is a Flask-based manufacturing console that unifies
inventory, production, purchasing, workstation, and reporting workflows behind a
single login. It targets lightweight industrial deployments (Intel NUC,
Raspberry Pi, or similar edge nodes) while scaling to centralized PostgreSQL.

The platform now embeds the KPI Board/"mdi-console" experience as the **MDI
module** so teams can run their daily Management for Daily Improvement cadence
without juggling a second service. All KPI dashboards, meeting views, CSV import
utilities, and API endpoints live under Hyperion's authentication, database, and
logging stack.

---

## Table of Contents
1. [Platform Overview](#platform-overview)
2. [MDI (KPI Board) Module](#mdi-kpi-board-module)
3. [How the Integration Works](#how-the-integration-works)
4. [Repository Layout](#repository-layout)
5. [Developer Setup](#developer-setup)
6. [Database Initialization](#database-initialization)
7. [Running the Application](#running-the-application)
8. [Workstation Queues](#workstation-queues)
9. [Accessing the MDI Dashboards](#accessing-the-mdi-dashboards)
10. [MDI Meeting Dashboard Experience](#mdi-meeting-dashboard-experience)
11. [Extending the MDI Module](#extending-the-mdi-module)
12. [Operational Tips](#operational-tips)

---

## Platform Overview

| Area | Highlights |
|------|------------|
| Inventory & Orders | Track items, batches, movements, reservations, and order BOMs with CSV import/export and audit history. |
| Production | Capture daily output, workstation queues, and printer-ready labels with Zebra integrations. |
| Purchasing & Quality | Manage purchase requests, RMAs, and quality notices including attachment storage. |
| Workstations | Serve instructions, schedules, and barcode workflows tailored for each station. |
| Reporting | Export bundled data packs and view live dashboards surfaced on the home screen. |
| Security | Role-based permissions, emergency offline access, and admin auditing baked into the app factory. |

---

## MDI (KPI Board) Module

The `invapp.mdi` package is the transplanted **kpi-board** (formerly
`mdi_console`) application. It adds:

* `/mdi/meeting` – Kanban-style meeting deck with category cards and CSV tools.
* `/mdi/report` – CRUD interface for MDI entries, attendance, shortages, etc.
* `/mdi/<category>` – Category dashboards for Safety, Quality, Delivery, People,
  and Materials with charts powered by seeded demo data.
* `/api/mdi_entries` – JSON API consumed by the dashboard JavaScript for live
  filtering and automation hooks.

All templates extend Hyperion's `base.html`, static assets live under
`invapp/static/mdi`, and SQLAlchemy models reuse the shared
`invapp.extensions.db` instance so KPI metrics are persisted in the same
PostgreSQL database as the rest of the platform.

---

## How the Integration Works

1. **Blueprint** – `invapp.mdi.mdi_bp` registers routes, templates, and static
   assets with `template_folder="../templates/mdi"` and
   `static_folder="../static/mdi"` to reuse Hyperion's directory tree.
2. **Routes** – The original `mdi_console` routes were converted into a single
   blueprint with modules for `meeting`, `dashboard`, `reports`, and `api`.
   Every route imports from `invapp.mdi` and uses `url_for('mdi.*')`, removing
   the duplicated Flask app from the donor project.
3. **Models** – `invapp.mdi.models` now imports `db` from `invapp.extensions`
   instead of instantiating its own `SQLAlchemy()`. The `ensure_schema()` helper
   runs during app startup alongside the legacy schema migrations to add the MDI
   tables/columns when missing, and `seed_data()` loads demo content.
4. **Templates & Static Files** – All donor HTML/JS/CSS moved into
   `invapp/templates/mdi` and `invapp/static/mdi`. Template references were
   updated to extend Hyperion's base layout and use the `mdi` blueprint’s
   endpoints.
5. **App Factory** – `invapp/__init__.py` imports `mdi_bp`, registers the
   blueprint, and invokes `mdi_models.ensure_schema()` plus
   `mdi_models.seed_data()` after the legacy schema preparation so new installs
   automatically expose the KPI features.

---

## Repository Layout

```
invapp2/
├── app.py
├── config.py
├── requirements.txt
├── start_inventory.sh
├── invapp/
│   ├── __init__.py          # App factory + blueprint registration (incl. MDI)
│   ├── extensions.py        # Shared db/login manager
│   ├── models.py            # Core inventory/production/auth models
│   ├── mdi/
│   │   ├── __init__.py      # mdi_bp blueprint definition
│   │   ├── models.py        # MDIEntry & CategoryMetric models + seed helpers
│   │   └── routes/
│   │       ├── api.py       # /api/mdi_entries CRUD
│   │       ├── dashboard.py # Safety/Quality/Delivery/People/Materials pages
│   │       ├── meeting.py   # /mdi/meeting deck
│   │       └── reports.py   # /mdi/report CRUD & CSV import/export
│   ├── routes/              # Inventory, orders, purchasing, etc.
│   ├── templates/
│   │   ├── base.html
│   │   └── mdi/
│   │       ├── base.html
│   │       ├── meeting_view.html
│   │       ├── report_entry.html
│   │       ├── components/*.html
│   │       └── category templates (safety, quality, delivery, people, materials)
│   └── static/
│       ├── js/, css/, uploads…
│       └── mdi/
│           ├── css/styles.css
│           └── js/{main.js, charts.js}
└── start_operations_console.sh
```

---

## Developer Setup

### Prerequisites
* Python 3.10+
* PostgreSQL 13+ with `libpq` headers (`libpq-dev`) and client libs (`libpq5`)
* `git`, `pip`, `setuptools`, `wheel`

### Clone & Install
```bash
git clone https://github.com/YOUR-ORG/Hyperion-Operations-Hub.git
cd Hyperion-Operations-Hub/invapp2
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

### Configure Environment
Set the database URL, secret key, and bootstrap credentials before launching:
```bash
export DB_URL="postgresql+psycopg2://USER:PASSWORD@localhost/invdb"
export SECRET_KEY="change_me"
export ADMIN_USER="superuser"
export ADMIN_PASSWORD="change_me"
```
Optional knobs (see `invapp2/config.py`) include printer hosts, attachment
extension allow-lists, and admin session timeouts.

---

## Database Initialization

Running `create_app()` automatically:
1. Pings the configured PostgreSQL instance.
2. Calls `db.create_all()` for the core models and the MDI models.
3. Applies legacy schema migrations (`_ensure_inventory_schema`, etc.).
4. Executes `mdi_models.ensure_schema()` and `mdi_models.seed_data()` to provision
   the KPI tables plus demo content.
5. Seeds the admin account and role definitions.

For a manual bootstrap:
```bash
cd Hyperion-Operations-Hub/invapp2
source .venv/bin/activate
flask --app app shell <<'PY'
from invapp import create_app
from invapp.extensions import db
app = create_app()
with app.app_context():
    db.create_all()
PY
```
The first launch performs the same migration + seeding steps, so no separate
migration tool is required.

---

## Running the Application

### Development Server
```bash
cd Hyperion-Operations-Hub/invapp2
source .venv/bin/activate
flask --app app run --debug
```

### Production-style (gunicorn)
```bash
cd Hyperion-Operations-Hub
./start_operations_console.sh
# or
cd invapp2
source .venv/bin/activate
gunicorn --bind 0.0.0.0:8000 app:app
```
`start_operations_console.sh` provisions the virtualenv, installs requirements,
checks database connectivity, and launches gunicorn pointed at `app:app`.

When PostgreSQL is unavailable the UI surfaces recovery guidance (service status
checks, restarting the DB, confirming `DB_URL`) while still allowing emergency
admin access so you can correct the outage.

---

## Workstation Queues

Framing operators can manage panel cutting directly from `/work/stations/framing`.
Admins see a **Panel Length Offset** input to control how much is subtracted from
each gate's total height when calculating the **Panel Length** shown in the
queue. The page also surfaces **Panels Needed** and **Panel Material** alongside
order and item details (falling back to the gate item number when SKU data is
missing). See [docs/workstations.md](docs/workstations.md) for a full breakdown
of the columns and offset workflow.

---

## Accessing the MDI Dashboards

* **MDI Meeting View** – `GET /mdi/meeting`
  * Defaults to the **Active (not Closed/Received)** filter so closed or received
    work is hidden on load.
  * Filter cards by category, status, or date.
  * Trigger CSV import/export and quick refresh actions.
* **MDI Report Entry** – `GET /mdi/report`
  * Provides a multi-category form with context-aware fields.
  * Submit via `POST /mdi/report/add` or update via
    `/mdi/report/update/<id>`.
* **Category Dashboards** – `GET /mdi/safety`, `/mdi/quality`, `/mdi/delivery`,
  `/mdi/people`, `/mdi/materials`
  * Show chart blocks, attendance summaries, shortages, and quick links.
* **JSON API** – `GET/POST/PUT/DELETE /api/mdi_entries`
  * Consumed by `invapp/static/mdi/js/main.js`; use the same endpoints for
    integrations or automations.

Read `docs/mdi-dashboard.md` for a facilitator-focused walkthrough of the
meeting experience. All routes inherit Hyperion's authentication/authorization
middleware, so access is controlled via the same role definitions as the rest of
the platform.

---

## MDI Meeting Dashboard Experience

The meeting dashboard is a Kanban-style deck optimized for daily production
standups:

* **Default view** – When the page loads it automatically applies the "Active
  (not Closed/Received)" filter. The status pill in the filter bar is set to the
  `not_closed_or_received` sentinel, and the JavaScript refresh loop persists
  that filter in the URL so live updates stay aligned with the server render.
* **Filters** – Users can switch category, status, and date via the form above
  the grid. Hitting "Apply Filters" updates the query string so the auto-refresh
  API calls stay scoped.
* **Auto-refreshing board** – The deck polls `/api/mdi_entries` every 60 seconds
  with the active filters. Each category lane summarizes how many items and
  metrics are present and renders cards for the filtered entries.
* **Fast actions** – Use **Add Item** to open the report form, **Export CSV** or
  **Upload CSV** for bulk edits, and the per-card **Mark Complete** button to set
  an item to `Closed`. Complete actions trigger a refresh so the card leaves the
  board when the active filter is in use.
* **Category context** – Each card surfaces the most relevant details for its
  category (Delivery due dates, People absences/open roles, Materials vendor &
  PO info, etc.) plus owner, priority, and date logged.

This flow keeps daily huddles focused on work that still needs attention while
retaining easy access to historical entries through the status filter.

---

## Extending the MDI Module

1. **Add metrics or categories** – Update `CATEGORY_DISPLAY`,
   `CATEGORY_SEQUENCE`, and `CATEGORY_METRIC_CONFIG` in
   `invapp/mdi/routes/dashboard.py`. New cards automatically appear in the
   meeting view once `CATEGORY_DISPLAY` includes them.
2. **Add persistence fields** – Extend `MDIEntry` or `CategoryMetric` in
   `invapp/mdi/models.py`. The `ensure_schema()` helper will append missing
   columns to existing databases when the app restarts.
3. **Expose new endpoints** – Define additional routes in
   `invapp/mdi/routes/*.py` and decorate them with `@mdi_bp.route(...)`. The
   template/static folders are already scoped to `invapp/templates/mdi` and
   `invapp/static/mdi` so Jinja includes remain local to the module.
4. **Seed additional demo data** – Modify `seed_data()` to pre-populate whatever
   KPIs help showcase your deployment; it only inserts data when the tables are
   empty.

---

## Operational Tips

* **Backups** – Schedule `pg_dump` jobs for the PostgreSQL database; both core
  inventory data and MDI KPIs live inside the same schema.
* **Static uploads** – Grant write access to `invapp/static` (including
  `invapp/static/mdi`) for the service account if running on hardened systems.
* **Diagnostics** – Use the admin "Emergency Console" (under Settings) if the
  database is offline; the UI exposes next-step commands to restart services.
* **Testing** – `pytest` tests live under `invapp2/tests`. Activate the virtual
  environment and run `pytest` before opening pull requests.

---

## License

Hyperion Operations Hub is released under the MIT License. See
[LICENSE](LICENSE) for details.
