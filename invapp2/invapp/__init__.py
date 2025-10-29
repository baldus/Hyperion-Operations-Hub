from datetime import date, timedelta

from flask import Flask, current_app, render_template, request, session, url_for
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError, NoSuchTableError, OperationalError, SQLAlchemyError
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm.exc import DetachedInstanceError

from .extensions import db, login_manager
from .offline import OfflineAdminUser
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
    purchasing,
    quality,
    printers,
    production,
    reports,
    settings,
    users,
    work,
)
from config import Config
from . import models  # ensure models are registered with SQLAlchemy
from .audit import record_access_event, resolve_client_ip


NAVIGATION_PAGES: tuple[tuple[str, str, str], ...] = (
    ("inventory", "inventory.inventory_home", "Inventory"),
    ("orders", "orders.orders_home", "Orders"),
    ("purchasing", "purchasing.purchasing_home", "Purchase Requests"),
    ("quality", "quality.quality_home", "Quality"),
    ("work", "work.station_overview", "Workstations"),
    ("production", "production.history", "Production History"),
)


def _ensure_superuser_account(admin_username: str, admin_password: str) -> None:
    """Create or update the default administrative user."""

    if not admin_username:
        return

    for attempt in range(3):
        try:
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
            return
        except IntegrityError:
            db.session.rollback()
            if attempt == 2:
                raise


def _ensure_core_roles() -> None:
    """Make sure the built-in platform roles exist for assignment."""

    desired_roles = {
        "public": "Unauthenticated read-only access",
        "viewer": "Read-only user",
        "editor": "Operations editor",
        "admin": "Administrator",
        "purchasing": "Purchasing team member",
        "quality": "Quality assurance specialist",
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

def _ensure_inventory_schema(engine):
    """Backfill legacy inventory tables with the current columns."""

    inspector = inspect(engine)

    try:
        inspector.get_columns("item_attachment")
    except (NoSuchTableError, OperationalError):
        metadata = db.Model.metadata
        if "item_attachment" in metadata.tables:
            metadata.tables["item_attachment"].create(bind=engine)

    try:
        item_columns = {col["name"] for col in inspector.get_columns("item")}
    except (NoSuchTableError, OperationalError):
        item_columns = set()

    item_columns_to_add = []
    item_required_columns = {
        "type": "VARCHAR",
        "notes": "TEXT",
        "list_price": "NUMERIC(12, 2)",
        "last_unit_cost": "NUMERIC(12, 2)",
        "item_class": "VARCHAR",
    }

    for column_name, column_type in item_required_columns.items():
        if column_name not in item_columns:
            item_columns_to_add.append(("item", column_name, column_type))

    try:
        batch_columns = {col["name"] for col in inspector.get_columns("batch")}
    except (NoSuchTableError, OperationalError):
        batch_columns = set()

    batch_required_columns = {
        "expiration_date": "DATE",
        "supplier_name": "VARCHAR",
        "supplier_code": "VARCHAR",
        "purchase_order": "VARCHAR",
        "notes": "TEXT",
    }

    for column_name, column_type in batch_required_columns.items():
        if column_name not in batch_columns:
            item_columns_to_add.append(("batch", column_name, column_type))

    if item_columns_to_add:
        with engine.begin() as conn:
            for table_name, column_name, column_type in item_columns_to_add:
                conn.execute(
                    text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                    )
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

    existing_tables = {table.lower() for table in inspector.get_table_names()}
    if "production_daily_gate_completion" not in existing_tables:
        metadata = db.Model.metadata
        gate_completion_table = metadata.tables.get("production_daily_gate_completion")
        if gate_completion_table is not None:
            gate_completion_table.create(bind=engine)

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

        def _queue_column_add(
            column_name: str,
            column_type: str,
            *,
            default: str | None = None,
            nullable: bool = True,
        ) -> None:
            if column_name in existing_column_names:
                return

            add_clause = (
                "ALTER TABLE production_daily_record "
                f"ADD COLUMN {column_name} {column_type}"
            )
            if default is not None:
                add_clause += f" DEFAULT {default}"
            if not nullable:
                add_clause += " NOT NULL"
            columns_to_add.append(add_clause)

        _queue_column_add("gates_employees", "INTEGER", default="0", nullable=False)
        _queue_column_add("gates_hours_ot", "NUMERIC(7, 2)", default="0", nullable=False)
        _queue_column_add("additional_employees", "INTEGER", default="0", nullable=False)
        _queue_column_add(
            "additional_hours_ot", "NUMERIC(7, 2)", default="0", nullable=False
        )
        _queue_column_add("gates_notes", "TEXT")
        _queue_column_add("gates_summary", "TEXT")
        _queue_column_add("additional_notes", "TEXT")
        _queue_column_add("additional_summary", "TEXT")

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

        if engine.dialect.name == "postgresql":
            managed_tables = (
                "production_daily_record",
                "production_daily_customer_total",
                "production_daily_gate_completion",
            )

            with engine.begin() as conn:
                for table_name in managed_tables:
                    if table_name not in existing_tables:
                        continue

                    sequence_name = conn.execute(
                        text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                        {"table_name": table_name},
                    ).scalar()

                    if not sequence_name:
                        continue

                    max_identifier = conn.execute(
                        text(f"SELECT COALESCE(MAX(id), 0) FROM {table_name}")
                    ).scalar()

                    if max_identifier is None:
                        max_identifier = 0

                    next_value = max_identifier if max_identifier > 0 else 1
                    conn.execute(
                        text("SELECT setval(:sequence_name, :value, :is_called)"),
                        {
                            "sequence_name": sequence_name,
                            "value": next_value,
                            "is_called": bool(max_identifier),
                        },
                    )


def _ping_database() -> None:
    """Raise :class:`OperationalError` when the configured database is unreachable."""

    with db.engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def create_app(config_override=None):
    app = Flask(__name__)

    # load configuration from environment variables
    app.config.from_object(Config)
    if config_override:
        app.config.update(config_override)

    # Track database health so the UI can surface meaningful guidance when the
    # backing service is offline.
    app.config.setdefault("DATABASE_AVAILABLE", True)
    app.config.setdefault("DATABASE_ERROR", None)
    app.config.setdefault(
        "DATABASE_RECOVERY_STEPS",
        (
            {
                "title": "Check PostgreSQL service status",
                "command": "sudo systemctl status postgresql",
            },
            {
                "title": "Start (or restart) the database",
                "command": "sudo systemctl start postgresql",
            },
            {
                "title": "Verify connection settings",
                "command": "echo \"$DB_URL\"",
            },
            {
                "title": "Relaunch the console",
                "command": "./start_operations_console.sh",
            },
        ),
    )

    database_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if database_uri.startswith("sqlite:///:memory:"):
        engine_options = app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {})
        connect_args = engine_options.setdefault("connect_args", {})
        connect_args.setdefault("check_same_thread", False)
        engine_options.setdefault("poolclass", StaticPool)

    # ✅ init db with app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.anonymous_user = OfflineAdminUser
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id: str):
        if not user_id:
            return None
        try:
            return models.User.query.get(int(user_id))
        except (TypeError, ValueError):
            return None
        except OperationalError:
            current_app.logger.warning(
                "Skipped user lookup during login_manager load because the database is unavailable."
            )
            return None

    database_available = True
    database_error_message: str | None = None

    # create tables if they do not exist and ensure legacy schema
    with app.app_context():
        try:
            _ping_database()
        except OperationalError as exc:
            database_available = False
            root_cause = getattr(exc, "orig", exc)
            details = str(root_cause).strip()
            database_error_message = (
                "Unable to connect to the configured database. Start the "
                "PostgreSQL service or update the DB_URL setting, then restart "
                "the console."
            )
            if details:
                database_error_message += f" (Error: {details})"
            message_suffix = f": {details}" if details else ""
            current_app.logger.error(
                "Database connection unavailable during startup%s",
                message_suffix,
                exc_info=current_app.debug,
            )
            db.session.remove()
            try:
                db.engine.dispose()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        else:
            try:
                db.create_all()
                _ensure_inventory_schema(db.engine)
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
            except SQLAlchemyError as exc:  # pragma: no cover - defensive guard
                database_available = False
                database_error_message = (
                    "The database schema could not be initialized. Review the logs "
                    "for details and re-run the startup script once resolved."
                )
                current_app.logger.exception("Database initialization error")
                db.session.remove()

    app.config["DATABASE_AVAILABLE"] = database_available
    app.config["DATABASE_ERROR"] = database_error_message

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
            "database_online": current_app.config.get("DATABASE_AVAILABLE", True),
            "database_error_message": current_app.config.get("DATABASE_ERROR"),
            "database_recovery_steps": current_app.config.get(
                "DATABASE_RECOVERY_STEPS", ()
            ),
            "emergency_access_active": bool(
                getattr(current_user, "is_emergency_user", False)
            ),
        }

    # register blueprints
    app.register_blueprint(auth.bp)
    app.register_blueprint(inventory.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(purchasing.bp)
    app.register_blueprint(quality.bp)
    app.register_blueprint(work.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(printers.bp)
    app.register_blueprint(production.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(users.bp)

    def _should_log_request() -> bool:
        if not request.endpoint:
            return False
        if request.method == "OPTIONS":
            return False
        if request.endpoint.startswith("static"):
            return False
        if request.path.startswith("/static/"):
            return False
        return True

    def _active_user_identity() -> tuple[int | None, str | None]:
        if not current_user.is_authenticated:
            return None, None

        user_id: int | None = None
        username: str | None = None

        raw_id = session.get("_user_id")
        try:
            user_id = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            user_id = None

        try:
            username = getattr(current_user, "username", None)
        except DetachedInstanceError:
            username = None

        if username is None and user_id is not None:
            refreshed = models.User.query.get(user_id)
            if refreshed is not None:
                username = refreshed.username

        return user_id, username

    @app.after_request
    def _record_request_log(response):
        if _should_log_request():
            path = request.full_path or request.path
            if path.endswith("?"):
                path = path[:-1]

            user_id, username = _active_user_identity()
            record_access_event(
                event_type=models.AccessLog.EVENT_REQUEST,
                user_id=user_id,
                username=username,
                ip_address=resolve_client_ip(),
                user_agent=request.user_agent.string if request.user_agent else None,
                method=request.method,
                path=path,
                endpoint=request.endpoint,
                status_code=response.status_code,
            )

        return response

    @app.route("/")
    def home():
        guard_response = ensure_page_access("home")
        if guard_response is not None:
            return guard_response

        if not current_app.config.get("DATABASE_AVAILABLE", True):
            return render_template(
                "home.html",
                order_summary=None,
                inventory_summary=None,
            )

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

