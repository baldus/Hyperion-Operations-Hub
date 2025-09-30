from datetime import date, timedelta

from flask import Flask, render_template, url_for
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import NoSuchTableError, OperationalError

from .extensions import db, login_manager
from .login import current_user
from .permissions import (
    current_principal_roles,
    ensure_page_access,
    lookup_page_label,
    principal_has_any_role,
    resolve_edit_roles,
    resolve_view_roles,
)
from .routes import (
    admin,
    auth,
    inventory,
    orders,
    printers,
    production,
    reports,
    settings,
    users,
    work,
)
from config import Config
from . import models  # ensure models are registered with SQLAlchemy


NAVIGATION_PAGES: tuple[tuple[str, str, str], ...] = (
    ("inventory", "inventory.inventory_home", "Inventory"),
    ("orders", "orders.orders_home", "Orders"),
    ("work", "work.work_home", "Work Instructions"),
    ("production", "production.history", "Production History"),
)


def _ensure_superuser_account(admin_username: str, admin_password: str) -> None:
    """Create or update the default administrative user."""

    if not admin_username:
        return

    admin_role = models.Role.query.filter_by(name="admin").first()
    if admin_role is None:
        admin_role = models.Role(name="admin", description="Administrator")
        db.session.add(admin_role)

    user = models.User.query.filter_by(username=admin_username).first()
    if user is None:
        user = models.User(username=admin_username)
        db.session.add(user)

    if admin_password:
        user.set_password(admin_password)

    if admin_role not in user.roles:
        user.roles.append(admin_role)

    db.session.commit()


def _ensure_core_roles() -> None:
    """Make sure the built-in platform roles exist for assignment."""

    desired_roles = {
        "public": "Unauthenticated read-only access",
        "viewer": "Read-only user",
        "editor": "Operations editor",
        "admin": "Administrator",
    }

    existing_roles = {
        role.name: role for role in models.Role.query.filter(models.Role.name.in_(desired_roles)).all()
    }

    created = False
    for role_name, description in desired_roles.items():
        if role_name in existing_roles:
            role = existing_roles[role_name]
            if role.description != description:
                role.description = description
            continue

        db.session.add(models.Role(name=role_name, description=description))
        created = True

    if created:
        db.session.commit()

def _ensure_item_columns(engine):
    """Ensure legacy databases include the latest ``item`` columns."""

    inspector = inspect(engine)
    try:
        item_columns = {col["name"] for col in inspector.get_columns("item")}
    except (NoSuchTableError, OperationalError):
        item_columns = set()

    columns_to_add = []
    required_columns = {
        "type": "VARCHAR",
        "notes": "TEXT",
        "list_price": "NUMERIC(12, 2)",
        "last_unit_cost": "NUMERIC(12, 2)",
        "item_class": "VARCHAR",
    }

    for column_name, column_type in required_columns.items():
        if column_name not in item_columns:
            columns_to_add.append((column_name, column_type))

    if columns_to_add:
        with engine.begin() as conn:
            for column_name, column_type in columns_to_add:
                conn.execute(
                    text(f"ALTER TABLE item ADD COLUMN {column_name} {column_type}")
                )


def _ensure_order_schema(engine):
    """Make sure legacy databases pick up the expanded order schema."""
    inspector = inspect(engine)
    existing_tables = {table.lower() for table in inspector.get_table_names()}
    required_tables = {
        "order_item",
        "order_bom_component",
        "order_step",
        "order_step_component",
        "item_bom",
        "item_bom_component",
    }
    missing_tables = required_tables - existing_tables
    if missing_tables:
        metadata = db.Model.metadata
        for table_name in missing_tables:
            metadata.tables[table_name].create(bind=engine)

    try:
        order_columns = {col["name"] for col in inspector.get_columns("order")}
    except (NoSuchTableError, OperationalError):
        order_columns = set()

    columns_to_add = []
    if "customer_name" not in order_columns:
        columns_to_add.append(("customer_name", "VARCHAR"))
    if "created_by" not in order_columns:
        columns_to_add.append(("created_by", "VARCHAR"))
    if "general_notes" not in order_columns:
        columns_to_add.append(("general_notes", "TEXT"))

    if columns_to_add:
        with engine.begin() as conn:
            for column_name, column_type in columns_to_add:
                conn.execute(
                    text(
                        f"ALTER TABLE \"order\" ADD COLUMN {column_name} {column_type}"
                    )
                )


def _ensure_production_schema(engine):
    """Align legacy production tables with the current model definitions."""

    inspector = inspect(engine)
    is_sqlite = engine.dialect.name == "sqlite"

    try:
        production_daily_columns = inspector.get_columns("production_daily_record")
    except (NoSuchTableError, OperationalError):
        production_daily_columns = None

    if production_daily_columns is not None:
        desired_length = 32
        needs_day_of_week_alter = False
        numeric_columns_missing_default: list[str] = []
        existing_column_names = {column["name"] for column in production_daily_columns}
        columns_to_add: list[str] = []

        for column in production_daily_columns:
            column_name = column["name"]
            if column_name == "day_of_week":
                current_type = column.get("type")
                current_length = getattr(current_type, "length", None)
                if current_length is not None and current_length < desired_length:
                    needs_day_of_week_alter = True

            if (
                column_name.startswith(("gates_produced_", "gates_packaged_"))
                and not column.get("nullable", True)
                and column.get("default") is None
            ):
                numeric_columns_missing_default.append(column_name)

        def _queue_column_add(column_name: str, column_type: str, default: str) -> None:
            if column_name in existing_column_names:
                return

            add_clause = (
                "ALTER TABLE production_daily_record "
                f"ADD COLUMN {column_name} {column_type} DEFAULT {default} NOT NULL"
            )
            columns_to_add.append(add_clause)

        _queue_column_add("gates_employees", "INTEGER", "0")
        _queue_column_add("gates_hours_ot", "NUMERIC(7, 2)", "0")
        _queue_column_add("additional_employees", "INTEGER", "0")
        _queue_column_add("additional_hours_ot", "NUMERIC(7, 2)", "0")

        if columns_to_add:
            with engine.begin() as conn:
                for statement in columns_to_add:
                    conn.execute(text(statement))

        # SQLite cannot alter existing column types or defaults easily. New
        # databases created via ``db.create_all()`` already include the desired
        # schema, so we only attempt these migrations on engines that support it.
        if not is_sqlite:
            if needs_day_of_week_alter:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE production_daily_record "
                            f"ALTER COLUMN day_of_week TYPE VARCHAR({desired_length})"
                        )
                    )

            if numeric_columns_missing_default:
                with engine.begin() as conn:
                    for column_name in numeric_columns_missing_default:
                        conn.execute(
                            text(
                                "ALTER TABLE production_daily_record "
                                f"ALTER COLUMN {column_name} SET DEFAULT 0"
                            )
                        )

    try:
        chart_columns = inspector.get_columns("production_chart_settings")
    except (NoSuchTableError, OperationalError):
        chart_columns = None

    if chart_columns is not None:
        existing_chart_columns = {column["name"] for column in chart_columns}
        boolean_defaults = {
            "show_trendline": False,
            "show_output_per_hour": False,
        }

        default_literals = {True: ("TRUE", "1"), False: ("FALSE", "0")}
        column_statements: list[str] = []

        for column_name, default_value in boolean_defaults.items():
            if column_name in existing_chart_columns:
                continue

            default_clause = default_literals[default_value][0]
            if is_sqlite:
                default_clause = default_literals[default_value][1]

            column_statements.append(
                "ALTER TABLE production_chart_settings "
                f"ADD COLUMN {column_name} BOOLEAN NOT NULL DEFAULT {default_clause}"
            )

        if column_statements:
            with engine.begin() as conn:
                for statement in column_statements:
                    conn.execute(text(statement))


def create_app(config_override=None):
    app = Flask(__name__)

    # load configuration from environment variables
    app.config.from_object(Config)
    if config_override:
        app.config.update(config_override)

    # ✅ init db with app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id: str):
        if not user_id:
            return None
        try:
            return models.User.query.get(int(user_id))
        except (TypeError, ValueError):
            return None

    # create tables if they do not exist and ensure legacy schema
    with app.app_context():
        db.create_all()
        _ensure_item_columns(db.engine)
        _ensure_order_schema(db.engine)
        _ensure_production_schema(db.engine)
        # ✅ ensure default production customers at startup
        production._ensure_default_customers()
        production._ensure_output_formula()
        _ensure_superuser_account(
            app.config.get("ADMIN_USER", "superuser"),
            app.config.get("ADMIN_PASSWORD", "joshbaldus"),
        )
        _ensure_core_roles()

    @app.context_processor
    def inject_permission_helpers():
        def can_access_page(page_name: str) -> bool:
            view_roles = resolve_view_roles(page_name)
            if not view_roles:
                return False
            return principal_has_any_role(view_roles)

        def can_edit_page(page_name: str) -> bool:
            edit_roles = resolve_edit_roles(page_name)
            if not edit_roles:
                return False
            return principal_has_any_role(edit_roles, require_auth=True)

        def navigation_links():
            links: list[dict[str, str]] = []
            for page_name, endpoint, display_label in NAVIGATION_PAGES:
                if not can_access_page(page_name):
                    continue
                try:
                    href = url_for(endpoint)
                except Exception:  # pragma: no cover - defensive guard
                    continue
                links.append(
                    {
                        "page_name": page_name,
                        "label": display_label,
                        "href": href,
                    }
                )
            return links

        return {
            "can_access_page": can_access_page,
            "can_edit_page": can_edit_page,
            "navigation_links": navigation_links,
            "current_principal_roles": current_principal_roles,
        }

    # register blueprints
    app.register_blueprint(auth.bp)
    app.register_blueprint(inventory.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(work.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(printers.bp)
    app.register_blueprint(production.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(users.bp)

    @app.route("/")
    def home():
        guard_response = ensure_page_access("home")
        if guard_response is not None:
            return guard_response

        order_summary = None
        inventory_summary = None

        can_view_orders = principal_has_any_role(resolve_view_roles("orders"))
        can_view_inventory = principal_has_any_role(resolve_view_roles("inventory"))

        if can_view_orders:
            today = date.today()
            due_soon_window = timedelta(days=3)
            soon_cutoff = today + due_soon_window
            active_statuses = tuple(models.OrderStatus.ACTIVE_STATES)

            due_soon_query = models.Order.query.filter(
                models.Order.status.in_(active_statuses),
                models.Order.promised_date.isnot(None),
                models.Order.promised_date >= today,
                models.Order.promised_date <= soon_cutoff,
            )
            due_soon_count = due_soon_query.count()
            due_soon_preview = (
                due_soon_query.order_by(
                    models.Order.promised_date.asc(),
                    models.Order.order_number.asc(),
                )
                .limit(5)
                .all()
            )

            overdue_query = models.Order.query.filter(
                models.Order.status.in_(active_statuses),
                models.Order.promised_date.isnot(None),
                models.Order.promised_date < today,
            )
            overdue_count = overdue_query.count()
            overdue_preview = (
                overdue_query.order_by(
                    models.Order.promised_date.asc(),
                    models.Order.order_number.asc(),
                )
                .limit(5)
                .all()
            )

            waiting_material_count = (
                models.Order.query.filter(
                    models.Order.status == models.OrderStatus.WAITING_MATERIAL
                ).count()
            )

            order_summary = {
                "due_soon_window_days": due_soon_window.days,
                "due_soon_count": due_soon_count,
                "due_soon_preview": due_soon_preview,
                "overdue_count": overdue_count,
                "overdue_preview": overdue_preview,
                "waiting_material_count": waiting_material_count,
                "preview_limit": 5,
            }

        if can_view_inventory:
            movement_totals = (
                db.session.query(
                    models.Movement.item_id,
                    func.coalesce(func.sum(models.Movement.quantity), 0).label(
                        "on_hand"
                    ),
                )
                .group_by(models.Movement.item_id)
                .all()
            )
            on_hand_map = {
                item_id: int(total or 0) for item_id, total in movement_totals
            }

            items = models.Item.query.order_by(models.Item.sku).all()

            low_items = []
            out_items = []
            for item in items:
                min_stock_raw = item.min_stock or 0
                try:
                    min_stock = int(min_stock_raw)
                except (TypeError, ValueError):
                    min_stock = 0
                if min_stock <= 0:
                    continue
                on_hand = on_hand_map.get(item.id, 0)
                shortage = max(min_stock - on_hand, 0)
                entry = {
                    "item": item,
                    "on_hand": on_hand,
                    "min_stock": min_stock,
                    "shortage": shortage,
                }
                if on_hand <= 0:
                    entry["is_out"] = True
                    out_items.append(entry)
                elif on_hand < min_stock:
                    entry["is_out"] = False
                    low_items.append(entry)

            out_items.sort(key=lambda entry: (-entry["shortage"], entry["item"].sku))
            low_items.sort(key=lambda entry: (-entry["shortage"], entry["item"].sku))

            preview_limit = 5
            inventory_preview = (out_items + low_items)[:preview_limit]

            inventory_summary = {
                "out_count": len(out_items),
                "low_count": len(low_items),
                "preview": inventory_preview,
                "preview_limit": preview_limit,
                "total_alerts": len(out_items) + len(low_items),
            }

        return render_template(
            "home.html",
            order_summary=order_summary,
            inventory_summary=inventory_summary,
        )

    return app

