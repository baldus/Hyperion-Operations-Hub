from decimal import Decimal
from datetime import datetime

from sqlalchemy import inspect
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import synonym
from sqlalchemy.orm.exc import DetachedInstanceError
from werkzeug.security import check_password_hash, generate_password_hash

from invapp.extensions import db
from invapp.login import UserMixin


class ProductionChartSettings(db.Model):
    __tablename__ = "production_chart_settings"

    id = db.Column(db.Integer, primary_key=True)
    primary_min = db.Column(db.Numeric(10, 2), nullable=True)
    primary_max = db.Column(db.Numeric(10, 2), nullable=True)
    primary_step = db.Column(db.Numeric(10, 2), nullable=True)
    secondary_min = db.Column(db.Numeric(10, 2), nullable=True)
    secondary_max = db.Column(db.Numeric(10, 2), nullable=True)
    secondary_step = db.Column(db.Numeric(10, 2), nullable=True)
    goal_value = db.Column(db.Numeric(10, 2), nullable=True)
    show_goal = db.Column(db.Boolean, nullable=False, default=False)

    @classmethod
    def get_or_create(cls):
        settings = cls.query.first()
        if settings is None:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings


class Printer(db.Model):
    __tablename__ = "printer"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    printer_type = db.Column(db.String(80), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def connection_label(self) -> str:
        port_display = f":{self.port}" if self.port else ""
        return f"{self.host}{port_display}"


class LabelTemplate(db.Model):
    __tablename__ = "label_template"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    trigger = db.Column(db.String(120), nullable=True)
    layout = db.Column(db.JSON, nullable=False, default=dict)
    fields = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class LabelProcessAssignment(db.Model):
    __tablename__ = "label_process_assignment"

    id = db.Column(db.Integer, primary_key=True)
    process = db.Column(db.String(120), nullable=False, unique=True)
    template_id = db.Column(db.Integer, db.ForeignKey("label_template.id"), nullable=False)
    template = db.relationship(
        "LabelTemplate",
        backref=db.backref("assignments", cascade="all, delete-orphan", lazy="joined"),
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Item(db.Model):
    __tablename__ = "item"
    id = db.Column(db.Integer, primary_key=True)  # system key
    sku = db.Column(db.String, unique=True, nullable=False)  # part number
    name = db.Column(db.String, nullable=False)
    type = db.Column(db.String)
    unit = db.Column(db.String, default="ea")
    description = db.Column(db.String)
    min_stock = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    list_price = db.Column(db.Numeric(12, 2))
    last_unit_cost = db.Column(db.Numeric(12, 2))
    item_class = db.Column(db.String)


class Location(db.Model):
    __tablename__ = "location"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String, unique=True, nullable=False)
    description = db.Column(db.String)


class Batch(db.Model):
    __tablename__ = "batch"
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    lot_number = db.Column(db.String, nullable=True)  # supplier batch/lot reference
    quantity = db.Column(db.Integer, default=0)
    received_date = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship("Item", backref="batches")


class Movement(db.Model):
    __tablename__ = "movement"
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey("batch.id"), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    movement_type = db.Column(db.String, nullable=False)  # RECEIPT, ISSUE, MOVE, ADJUST
    person = db.Column(db.String, nullable=True)
    po_number = db.Column(db.String, nullable=True)
    reference = db.Column(db.String, nullable=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship("Item", backref="movements")
    batch = db.relationship("Batch", backref="movements")
    location = db.relationship("Location", backref="movements")


class WorkInstruction(db.Model):
    __tablename__ = "work_instruction"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String, nullable=False)
    original_name = db.Column(db.String, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)



class ProductionCustomer(db.Model):
    __tablename__ = "production_customer"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    color = db.Column(db.String(7), nullable=False, default="#3b82f6")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_other_bucket = db.Column(db.Boolean, nullable=False, default=False)
    lump_into_other = db.Column(db.Boolean, nullable=False, default=False)

    totals = db.relationship(
        "ProductionDailyCustomerTotal",
        back_populates="customer",
        cascade="all, delete-orphan",
    )



class ProductionDailyRecord(db.Model):
    __tablename__ = "production_daily_record"

    id = db.Column(db.Integer, primary_key=True)
    entry_date = db.Column(db.Date, unique=True, index=True, nullable=False)
    # Allow room for locales with longer weekday names (e.g., "Donnerstag")
    day_of_week = db.Column(db.String(32), nullable=False)


    gates_employees = db.Column(db.Integer, nullable=False, default=0)
    gates_hours_ot = db.Column(db.Numeric(7, 2), nullable=False, default=0)
    controllers_4_stop = db.Column(db.Integer, nullable=False, default=0)
    controllers_6_stop = db.Column(db.Integer, nullable=False, default=0)
    door_locks_lh = db.Column(db.Integer, nullable=False, default=0)
    door_locks_rh = db.Column(db.Integer, nullable=False, default=0)
    operators_produced = db.Column(db.Integer, nullable=False, default=0)
    cops_produced = db.Column(db.Integer, nullable=False, default=0)
    additional_employees = db.Column(db.Integer, nullable=False, default=0)
    additional_hours_ot = db.Column(db.Numeric(7, 2), nullable=False, default=0)
    daily_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


    customer_totals = db.relationship(
        "ProductionDailyCustomerTotal",
        back_populates="record",
        cascade="all, delete-orphan",
        lazy="joined",
    )

    LABOR_SHIFT_HOURS = Decimal("8.0")

    @property
    def total_gates_produced(self) -> int:
        return sum(total.gates_produced or 0 for total in self.customer_totals)

    @property
    def total_gates_packaged(self) -> int:
        return sum(total.gates_packaged or 0 for total in self.customer_totals)


    @property
    def total_controllers(self) -> int:
        return (self.controllers_4_stop or 0) + (self.controllers_6_stop or 0)

    @property
    def total_door_locks(self) -> int:
        return (self.door_locks_lh or 0) + (self.door_locks_rh or 0)

    @property
    def gates_total_labor_hours(self) -> Decimal:
        employees = Decimal(self.gates_employees or 0)
        overtime = self.gates_hours_ot or Decimal("0")
        return (employees * self.LABOR_SHIFT_HOURS + overtime).quantize(
            Decimal("0.01")
        )

    @property
    def additional_total_labor_hours(self) -> Decimal:
        employees = Decimal(self.additional_employees or 0)
        overtime = self.additional_hours_ot or Decimal("0")
        return (employees * self.LABOR_SHIFT_HOURS + overtime).quantize(
            Decimal("0.01")
        )


class ProductionDailyCustomerTotal(db.Model):
    __tablename__ = "production_daily_customer_total"

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(
        db.Integer,
        db.ForeignKey("production_daily_record.id"),
        nullable=False,
    )
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("production_customer.id"),
        nullable=False,
    )
    gates_produced = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged = db.Column(db.Integer, nullable=False, default=0)

    record = db.relationship(
        "ProductionDailyRecord",
        back_populates="customer_totals",
    )
    customer = db.relationship(
        "ProductionCustomer",
        back_populates="totals",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "record_id",
            "customer_id",
            name="uq_production_record_customer",
        ),
    )



class ProductionOutputFormula(db.Model):
    __tablename__ = "production_output_formula"

    id = db.Column(db.Integer, primary_key=True)
    formula = db.Column(db.Text, nullable=False)
    variables = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


user_roles = db.Table(
    "user_role",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
)


page_access_roles = db.Table(
    "page_access_role",
    db.Column(
        "access_rule_id",
        db.Integer,
        db.ForeignKey("page_access_rule.id"),
        primary_key=True,
    ),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
)


page_edit_roles = db.Table(
    "page_edit_role",
    db.Column(
        "access_rule_id",
        db.Integer,
        db.ForeignKey("page_access_rule.id"),
        primary_key=True,
    ),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
)


class Role(db.Model):
    __tablename__ = "role"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(255))

    users = db.relationship(
        "User",
        secondary=user_roles,
        back_populates="roles",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Role {self.name}>"


class PageAccessRule(db.Model):
    __tablename__ = "page_access_rule"

    id = db.Column(db.Integer, primary_key=True)
    page_name = db.Column(db.String(128), unique=True, nullable=False)
    label = db.Column(db.String(255), nullable=False)

    view_roles = db.relationship(
        "Role",
        secondary=page_access_roles,
        lazy="joined",
    )

    edit_roles = db.relationship(
        "Role",
        secondary=page_edit_roles,
        lazy="joined",
    )

    @property
    def roles(self):
        """Backward compatible alias for ``view_roles``."""

        return self.view_roles

    @roles.setter
    def roles(self, roles):
        self.view_roles = roles

    def assigned_role_names(self) -> list[str]:
        return sorted({role.name for role in self.view_roles})

    def assigned_view_role_names(self) -> list[str]:
        return sorted({role.name for role in self.view_roles})

    def assigned_edit_role_names(self) -> list[str]:
        return sorted({role.name for role in self.edit_roles})

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<PageAccessRule {self.page_name} "
            f"view={self.assigned_view_role_names()} "
            f"edit={self.assigned_edit_role_names()}>"
        )


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    roles = db.relationship(
        "Role",
        secondary=user_roles,
        back_populates="users",
        lazy="joined",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def has_role(self, role_name: str) -> bool:
        return self.has_any_role((role_name,))

    def has_any_role(self, role_names) -> bool:
        if not role_names:
            return False

        try:
            role_name_set = {role.name for role in self.roles}
        except DetachedInstanceError:
            identity = inspect(self).identity
            if not identity:
                return False
            refreshed = db.session.get(User, identity[0])
            if refreshed is None:
                return False
            return refreshed.has_any_role(role_names)
        except TypeError:
            return False

        return any(name in role_name_set for name in role_names)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User {self.username}>"



class OrderStatus:
    SCHEDULED = "SCHEDULED"
    OPEN = "OPEN"
    WAITING_MATERIAL = "WAITING_MATERIAL"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"

    ACTIVE_STATES = {SCHEDULED, OPEN, WAITING_MATERIAL}
    RESERVABLE_STATES = {SCHEDULED, OPEN}
    ALL_STATUSES = [SCHEDULED, OPEN, WAITING_MATERIAL, CLOSED, CANCELLED]
    LABELS = {
        SCHEDULED: "Scheduled",
        OPEN: "Open",
        WAITING_MATERIAL: "Waiting on Material",
        CLOSED: "Closed",
        CANCELLED: "Cancelled",
    }


class Order(db.Model):
    __tablename__ = "order"

    __table_args__ = (
        db.CheckConstraint(
            "(scheduled_start_date IS NULL) OR "
            "(scheduled_completion_date IS NULL) OR "
            "(scheduled_start_date <= scheduled_completion_date)",
            name="ck_order_schedule_window",
        ),
        db.CheckConstraint(
            "(promised_date IS NULL) OR (scheduled_completion_date IS NULL) OR "
            "(promised_date >= scheduled_completion_date)",
            name="ck_order_promised_after_completion",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String, unique=True, nullable=False)
    status = db.Column(db.String, nullable=False, default=OrderStatus.SCHEDULED)
    customer_name = db.Column(db.String, nullable=True)
    created_by = db.Column(db.String, nullable=True)
    general_notes = db.Column(db.Text, nullable=True)
    promised_date = db.Column(db.Date, nullable=True)
    scheduled_start_date = db.Column(db.Date, nullable=True)
    scheduled_completion_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    order_lines = db.relationship(
        "OrderLine",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderLine.id",
    )
    items = synonym("order_lines")
    routing_steps = db.relationship(
        "RoutingStep",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="RoutingStep.sequence",
    )
    steps = synonym("routing_steps")

    def __repr__(self):
        return f"<Order {self.order_number} status={self.status}>"

    @property
    def primary_line(self):
        return self.order_lines[0] if self.order_lines else None

    @property
    def primary_item(self):
        return self.primary_line

    @property
    def routing_progress(self):
        if not self.routing_steps:
            return None
        completed = sum(1 for step in self.routing_steps if step.completed)
        total = len(self.routing_steps)
        if total == 0:
            return None
        return completed / total

    @property
    def status_label(self):
        if not self.status:
            return "—"
        return OrderStatus.LABELS.get(
            self.status, self.status.replace("_", " ").title()
        )


class OrderLine(db.Model):
    __tablename__ = "order_item"

    __table_args__ = (
        db.CheckConstraint(
            "(scheduled_start_date IS NULL) OR "
            "(scheduled_completion_date IS NULL) OR "
            "(scheduled_start_date <= scheduled_completion_date)",
            name="ck_order_line_schedule_window",
        ),
        db.CheckConstraint(
            "(promised_date IS NULL) OR (scheduled_completion_date IS NULL) OR "
            "(promised_date >= scheduled_completion_date)",
            name="ck_order_line_promised_after_completion",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    promised_date = db.Column(db.Date, nullable=True)
    scheduled_start_date = db.Column(db.Date, nullable=True)
    scheduled_completion_date = db.Column(db.Date, nullable=True)

    order = db.relationship("Order", back_populates="order_lines")
    item = db.relationship("Item")
    components = db.relationship(
        "OrderComponent",
        back_populates="order_line",
        cascade="all, delete-orphan",
        order_by="OrderComponent.id",
    )
    bom_components = synonym("components")
    reservations = db.relationship(
        "Reservation",
        back_populates="order_line",
        cascade="all, delete-orphan",
        order_by="Reservation.id",
    )

    def __repr__(self):
        return (
            f"<OrderLine order={self.order_id} item={self.item_id} qty={self.quantity}>"
        )


class OrderComponent(db.Model):
    __tablename__ = "order_bom_component"

    __table_args__ = (
        db.UniqueConstraint(
            "order_item_id", "component_item_id", name="uq_order_component_item"
        ),
        db.CheckConstraint("quantity > 0", name="ck_order_component_quantity_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_line_id = db.Column(
        "order_item_id", db.Integer, db.ForeignKey("order_item.id"), nullable=False
    )
    component_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    order_line = db.relationship("OrderLine", back_populates="components")
    order_item = synonym("order_line")
    component_item = db.relationship("Item")
    routing_step_links = db.relationship(
        "RoutingStepComponent",
        back_populates="order_component",
        cascade="all, delete-orphan",
        order_by="RoutingStepComponent.id",
    )
    step_usages = synonym("routing_step_links")
    routing_steps = association_proxy("routing_step_links", "routing_step")

    def __repr__(self):
        return (
            f"<OrderComponent order_line={self.order_line_id} "
            f"component={self.component_item_id} qty={self.quantity}>"
        )


class BillOfMaterial(db.Model):
    __tablename__ = "item_bom"

    __table_args__ = (
        db.UniqueConstraint("item_id", name="uq_bom_item"),
    )

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    item = db.relationship("Item", backref=db.backref("bill_of_material", uselist=False))
    components = db.relationship(
        "BillOfMaterialComponent",
        back_populates="bom",
        cascade="all, delete-orphan",
        order_by="BillOfMaterialComponent.id",
    )

    def __repr__(self):
        return (
            f"<BillOfMaterial item={self.item_id} "
            f"components={len(self.components)}>"
        )


class BillOfMaterialComponent(db.Model):
    __tablename__ = "item_bom_component"

    __table_args__ = (
        db.UniqueConstraint(
            "bom_id", "component_item_id", name="uq_bom_component_item"
        ),
        db.CheckConstraint(
            "quantity > 0", name="ck_bom_component_quantity_positive"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.Integer, db.ForeignKey("item_bom.id"), nullable=False)
    component_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    bom = db.relationship("BillOfMaterial", back_populates="components")
    component_item = db.relationship("Item")

    def __repr__(self):
        return (
            f"<BillOfMaterialComponent bom={self.bom_id} "
            f"component={self.component_item_id} qty={self.quantity}>"
        )


class RoutingStep(db.Model):
    __tablename__ = "order_step"

    __table_args__ = (
        db.UniqueConstraint("order_id", "sequence", name="uq_routing_step_sequence"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    sequence = db.Column(db.Integer, nullable=False)
    work_cell = db.Column(db.String, nullable=True)
    description = db.Column(db.String, nullable=False)
    completed = db.Column(db.Boolean, default=False, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    order = db.relationship("Order", back_populates="routing_steps")
    component_links = db.relationship(
        "RoutingStepComponent",
        back_populates="routing_step",
        cascade="all, delete-orphan",
        order_by="RoutingStepComponent.id",
    )
    component_usages = synonym("component_links")
    components = association_proxy("component_links", "order_component")

    def __repr__(self):
        return (
            f"<RoutingStep order={self.order_id} seq={self.sequence} "
            f"completed={self.completed}>"
        )


class RoutingStepComponent(db.Model):
    __tablename__ = "order_step_component"

    __table_args__ = (
        db.UniqueConstraint(
            "order_step_id",
            "order_bom_component_id",
            name="uq_routing_step_component_usage",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    routing_step_id = db.Column(
        "order_step_id", db.Integer, db.ForeignKey("order_step.id"), nullable=False
    )
    order_component_id = db.Column(
        "order_bom_component_id",
        db.Integer,
        db.ForeignKey("order_bom_component.id"),
        nullable=False,
    )

    routing_step = db.relationship("RoutingStep", back_populates="component_links")
    order_component = db.relationship("OrderComponent", back_populates="routing_step_links")
    order_step = synonym("routing_step")
    bom_component = synonym("order_component")
    consumptions = db.relationship(
        "RoutingStepConsumption",
        back_populates="routing_step_component",
        cascade="all, delete-orphan",
        order_by="RoutingStepConsumption.id",
    )

    def __repr__(self):
        return (
            f"<RoutingStepComponent step={self.routing_step_id} "
            f"order_component={self.order_component_id}>"
        )


class RoutingStepConsumption(db.Model):
    __tablename__ = "order_step_consumption"

    id = db.Column(db.Integer, primary_key=True)
    routing_step_component_id = db.Column(
        db.Integer,
        db.ForeignKey("order_step_component.id"),
        nullable=False,
    )
    movement_id = db.Column(db.Integer, db.ForeignKey("movement.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    routing_step_component = db.relationship(
        "RoutingStepComponent", back_populates="consumptions"
    )
    movement = db.relationship("Movement")

    def __repr__(self):
        return (
            f"<RoutingStepConsumption usage={self.routing_step_component_id} "
            f"movement={self.movement_id} qty={self.quantity}>"
        )


class Reservation(db.Model):
    __tablename__ = "reservation"

    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_reservation_positive_quantity"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_line_id = db.Column(
        "order_item_id", db.Integer, db.ForeignKey("order_item.id"), nullable=False
    )
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    order_line = db.relationship("OrderLine", back_populates="reservations")
    order_item = synonym("order_line")
    item = db.relationship("Item")

    def __repr__(self):
        return (
            f"<Reservation order_line={self.order_line_id} item={self.item_id} "
            f"qty={self.quantity}>"
        )


# Backwards compatibility aliases for legacy imports
OrderItem = OrderLine
OrderBOMComponent = OrderComponent
OrderStep = RoutingStep
OrderStepComponent = RoutingStepComponent
