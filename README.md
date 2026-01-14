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
7. [Inventory Data Model](#inventory-data-model)
8. [Item Locations](#item-locations)
9. [CSV Import/Export](#csv-importexport)
10. [Migrations](#migrations)
11. [Tests](#tests)
12. [Developer Notes](#developer-notes)
13. [Troubleshooting](#troubleshooting)
14. [Running the Application](#running-the-application)
15. [Workstation Queues](#workstation-queues)
16. [Accessing the MDI Dashboards](#accessing-the-mdi-dashboards)
17. [MDI Meeting Dashboard Experience](#mdi-meeting-dashboard-experience)
18. [Extending the MDI Module](#extending-the-mdi-module)
19. [Operational Tips](#operational-tips)
20. [Open Orders Imports](#open-orders-imports)
21. [Home Screen Cubes](#home-screen-cubes)

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

### Backup Configuration
Automated backups write into a resolved backup directory. The application will
attempt the following, in order:

1. `BACKUP_DIR` environment variable
2. `BACKUP_DIR` app config value
3. `<instance_path>/backups` (default)
4. `./backups` (fallback)

If a configured path is not writable, the app logs a warning and falls back to
the next option without blocking startup.

Database restores are restricted to the configured superuser account and
require explicit UI confirmations before execution.

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
The first launch performs the same migration + seeding steps for legacy schema
alignment. Use Alembic (see [Migrations](#migrations)) for tracked schema
changes going forward.

---

## Inventory Data Model

Core inventory data lives in `invapp/models.py` and uses the following tables
and relationships:

* **Item** (`Item`, table `item`) – the SKU/master record for inventory items.
* **Location** (`Location`, table `location`) – storage or usage locations.
* **Movement** (`Movement`, table `movement`) – stock ledger entries; on-hand
  quantities are derived by summing `Movement.quantity` per `item_id` and
  `location_id`.
* **Batch** (`Batch`, table `batch`) – lot/batch records linked to items and
  (optionally) movements.

### Removing stock from a location

Removing inventory from a location records a stock movement entry (type
`REMOVE_FROM_LOCATION`) instead of deleting items, batches, or locations. The
movement uses a negative quantity to zero or reduce the on-hand balance for the
target item/lot/location combination. Reasons for removal are configurable via
the `INVENTORY_REMOVE_REASONS` app setting (comma-separated list). See
`invapp/routes/inventory.py` for the handling logic and inventory UI routes.

### Pending quantity receipts

Receipts recorded without a quantity are stored as `Movement` rows with
`movement_type="RECEIPT"`, `quantity=0`, and a reference containing the marker
`quantity pending`. Location views include these rows (flagged with a “Qty
Pending” badge), while aggregate sums continue to ignore them because the
quantity is zero. Setting a quantity resolves the pending marker and logs a new
receipt movement for the counted quantity, so totals remain accurate. Batch
counts include both pending and counted batches, but on-hand totals ignore
pending quantities.

Relationship map (simplified):

```
Item (item.id)
  ├─ default_location_id -> Location (location.id)
  ├─ secondary_location_id -> Location (location.id)
  ├─ point_of_use_location_id -> Location (location.id)
  └─ Movement (movement.item_id, movement.location_id)
         └─ Batch (batch.id) optional via movement.batch_id
```

---

## Item Locations

Item-level location fields define preferred storage and usage points:

* **Primary Location** – `Item.default_location_id` (existing field, treated as
  the primary/default storage location).
* **Secondary Location** – `Item.secondary_location_id` (optional overflow or
  alternate storage).
* **Point-of-Use (POU) Location** – `Item.point_of_use_location_id` (optional,
  manually managed, never auto-assigned).

Validation rules enforced in the Item create/edit UI and CSV imports:

* Primary and Secondary must be different.
* Primary and POU must be different.
* Secondary and POU must be different.

### Smart Location Assignment

When receiving or moving inventory into a selected location, the application
applies these rules (implemented in `invapp/services/item_locations.py`):

1. **No primary set** → assign the selected location as the primary.
2. **Primary set**:
   * If selected location == primary → do nothing.
   * If selected location != primary → check whether the primary location has
     on-hand stock for the item (sum of `Movement.quantity` at the primary).
   * If primary has stock **and** Secondary is empty → set Secondary to the
     selected location.
   * If Secondary is already set → do nothing (no overwrites).
3. **POU is never auto-assigned.** It can only be set in the Item UI or via CSV
   import fields.

Examples:

* **Example A:** Item has no primary. Receiving into `LOC-A` → primary becomes
  `LOC-A`.
* **Example B:** Item primary is `LOC-A` and has stock. Receiving into `LOC-B`
  with no secondary set → secondary becomes `LOC-B`.
* **Example C:** Item secondary already set → receiving into `LOC-C` does not
  overwrite the existing secondary.
* **Example D:** POU is never auto-set, even if primary/secondary update.

---

## CSV Import/Export

Item CSV import/export is handled in `invapp/routes/inventory.py` using the
schemas in `invapp/utils/csv_schema.py`.

### Item CSV headers (export + import mapping)

* `item_id`, `sku`, `name`, `type`, `unit`, `description`, `min_stock`,
  `notes`, `list_price`, `last_unit_cost`, `item_class`
* `default_location_id`, `default_location_code`
* `secondary_location_id`, `secondary_location_code`
* `point_of_use_location_id`, `point_of_use_location_code`

Import behavior:

* Location fields match by **ID first**, then by **location code** if the ID
  is blank or invalid.
* Imports **do not** trigger smart location assignment; they set exactly what
  the CSV provides.
* Duplicate location selections (primary/secondary/POU) are rejected and the
  affected rows are skipped with a warning.

Stock CSV import/export remains available via `/inventory/stock/import` and
`/inventory/stock/export`, using `location_id`/`location_code` plus batch/lot
columns for on-hand adjustments.

---

## Migrations

Alembic migrations live in `invapp2/migrations`. To apply migrations:

```bash
cd Hyperion-Operations-Hub/invapp2
export DB_URL="postgresql+psycopg2://USER:PASSWORD@localhost/invdb"
alembic upgrade head
```

To create a new revision:

```bash
alembic revision -m "describe change"
```

---

## Tests

Run the full test suite from `invapp2`:

```bash
pytest
```

Smart location assignment tests live in `tests/test_item_locations.py`.

---

## Developer Notes

### Schema Discovery Notes

* **Item model:** `invapp/models.py` (`Item`, table `item`).
* **Location model:** `invapp/models.py` (`Location`, table `location`).
* **On-hand inventory ledger:** `invapp/models.py` (`Movement`, table
  `movement`) with sums of `Movement.quantity` per location.
* **Lot/batch records:** `invapp/models.py` (`Batch`, table `batch`).
* **Receiving routes:** `invapp/routes/inventory.py` (`/inventory/receiving`)
  and `invapp/routes/receiving.py` (`/receiving`).
* **Stock transfer routes/services:** `invapp/routes/inventory.py`
  (`/inventory/stock/<item_id>/transfer`, `/inventory/move`) and
  `invapp/services/stock_transfer.py`.
* **CSV import/export paths:** `invapp/routes/inventory.py`
  (`/inventory/items/import`, `/inventory/items/export`, `/inventory/stock/import`,
  `/inventory/stock/export`) with schemas in `invapp/utils/csv_schema.py`.
* **Stock Overview primary location:** the Stock Overview table reads the
  primary location from `Item.default_location` (joined in
  `_stock_overview_query`), and exports it via the stock CSV column
  `primary_location_code`.

---

## Troubleshooting

* **Missing column or relation errors after deploy**
  * Run `alembic upgrade head` and verify `DB_URL` points at the correct
    database.
  * Use `flask --app app open-orders-schema-audit` to list missing Open Orders
    columns.
  * For single-instance dev environments, you can set
    `AUTO_MIGRATE_ON_STARTUP=true` to run `alembic upgrade head` at boot.
* **Foreign key violations when importing items**
  * Ensure the referenced `location.id` or `location.code` exists before import.
* **Multiple Alembic heads**
  * Run `alembic heads` and merge the revisions or pick the correct branch.
* **Verify item location columns**
  * In `psql`, run: `\\d item` and confirm `secondary_location_id` and
    `point_of_use_location_id` exist.
* **Postgres GROUP BY errors on Stock Overview**
  * The Stock Overview aggregates movements in a subquery to avoid strict
    `GROUP BY` issues when joining optional location metadata; ensure local
    changes keep that aggregation pattern intact.

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

## Open Orders Imports

Open Orders uploads keep historical lines instead of deleting them. Lines that
disappear from the newest upload are marked as `complete` with a completion
timestamp, and the dashboard filter lets users switch between **Open**,
**Completed**, and **All** lines. Click a sales order number to open the order
detail page and attach notes or action items that persist across future uploads.

---

## Home Screen Cubes

Home tiles are registered in `invapp2/invapp/home_cubes.py`. To add a new cube:

1. Add a new `CubeDefinition` entry to `HOME_CUBES` with a stable `key`, label,
   description, and `endpoint` route.
2. Decide whether the cube should be enabled by default. Add its key to
   `DEFAULT_HOME_CUBE_KEYS` if it should appear immediately for users without a
   saved layout.
3. Update the `home` route in `invapp2/invapp/__init__.py` to compute any data
   needed by the cube.
4. Add the cube markup to `invapp2/invapp/templates/home.html` in the cube loop
   so it can render when visible.

Once registered, the cube automatically appears in the “Available cubes” list
for users who have access to the associated page.

---

## License

Hyperion Operations Hub is released under the MIT License. See
[LICENSE](LICENSE) for details.
