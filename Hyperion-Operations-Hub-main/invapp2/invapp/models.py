from datetime import datetime

from flask_login import UserMixin
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy import inspect
from sqlalchemy.orm import joinedload, synonym
from sqlalchemy.orm.exc import DetachedInstanceError
from werkzeug.security import check_password_hash, generate_password_hash

from invapp.extensions import db


user_roles = db.Table(
    "user_roles",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
)


class Role(db.Model):
    __tablename__ = "role"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)

    def __repr__(self):
        return f"<Role name={self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)

    roles = db.relationship(
        Role,
        secondary=user_roles,
        backref=db.backref("users", lazy="dynamic"),
        lazy="joined",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def has_role(self, role_name: str) -> bool:
        try:
            return any(role.name == role_name for role in self.roles)
        except DetachedInstanceError:
            identity = inspect(self).identity
            if not identity:
                return False
            user_id = identity[0]
            user = (
                db.session.query(type(self))
                .options(joinedload(type(self).roles))
                .filter_by(id=user_id)
                .one_or_none()
            )
            if not user:
                return False
            return any(role.name == role_name for role in user.roles)

    def __repr__(self):
        return f"<User username={self.username}>"


class BillOfMaterial(db.Model):
    __tablename__ = "bill_of_material"

    __table_args__ = (
        db.UniqueConstraint(
            "finished_good_item_id",
            "revision",
            name="uq_bom_finished_good_revision",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    finished_good_item_id = db.Column(
        db.Integer, db.ForeignKey("item.id"), nullable=False
    )
    description = db.Column(db.String, nullable=True)
    revision = db.Column(db.String(32), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    finished_good = db.relationship(
        "Item",
        backref=db.backref("bill_of_materials", cascade="all, delete-orphan"),
    )

    components = db.relationship(
        "BillOfMaterialComponent",
        back_populates="bill_of_material",
        cascade="all, delete-orphan",
        order_by="BillOfMaterialComponent.id",
    )

    def __repr__(self):
        revision_label = f" rev={self.revision}" if self.revision else ""
        return (
            f"<BillOfMaterial fg={self.finished_good_item_id}{revision_label} "
            f"components={len(self.components) if self.components else 0}>"
        )


class BillOfMaterialComponent(db.Model):
    __tablename__ = "bill_of_material_component"

    __table_args__ = (
        db.UniqueConstraint(
            "bill_of_material_id",
            "component_item_id",
            name="uq_bom_component_item",
        ),
        db.CheckConstraint(
            "quantity > 0", name="ck_bom_component_positive_quantity"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    bill_of_material_id = db.Column(
        db.Integer, db.ForeignKey("bill_of_material.id"), nullable=False
    )
    component_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    bill_of_material = db.relationship(
        "BillOfMaterial", back_populates="components"
    )
    component_item = db.relationship("Item")

    def __repr__(self):
        return (
            f"<BillOfMaterialComponent bom={self.bill_of_material_id} "
            f"component={self.component_item_id} qty={self.quantity}>"
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
