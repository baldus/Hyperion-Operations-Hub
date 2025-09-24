from datetime import datetime

from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import synonym

from invapp.extensions import db

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


class ProductionDailyRecord(db.Model):
    __tablename__ = "production_daily_record"

    id = db.Column(db.Integer, primary_key=True)
    entry_date = db.Column(db.Date, unique=True, index=True, nullable=False)
    day_of_week = db.Column(db.String(9), nullable=False)

    gates_produced_ahe = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_bella = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_rei = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_savaria = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_eleshi = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_mornst = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_maine = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_garpa = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_dmeacc = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_admy = db.Column(db.Integer, nullable=False, default=0)
    gates_produced_other = db.Column(db.Integer, nullable=False, default=0)

    gates_packaged_ahe = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_bella = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_rei = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_savaria = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_eleshi = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_mornst = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_maine = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_garpa = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_dmeacc = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_admy = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged_other = db.Column(db.Integer, nullable=False, default=0)

    controllers_4_stop = db.Column(db.Integer, nullable=False, default=0)
    controllers_6_stop = db.Column(db.Integer, nullable=False, default=0)
    door_locks_lh = db.Column(db.Integer, nullable=False, default=0)
    door_locks_rh = db.Column(db.Integer, nullable=False, default=0)
    operators_produced = db.Column(db.Integer, nullable=False, default=0)
    cops_produced = db.Column(db.Integer, nullable=False, default=0)
    daily_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    @property
    def total_gates_produced(self) -> int:
        return sum(
            getattr(self, field)
            for field in _GATE_PRODUCED_FIELDS
        )

    @property
    def total_gates_packaged(self) -> int:
        return sum(
            getattr(self, field)
            for field in _GATE_PACKAGED_FIELDS
        )

    @property
    def total_controllers(self) -> int:
        return (self.controllers_4_stop or 0) + (self.controllers_6_stop or 0)

    @property
    def total_door_locks(self) -> int:
        return (self.door_locks_lh or 0) + (self.door_locks_rh or 0)


_GATE_PRODUCED_FIELDS = [
    "gates_produced_ahe",
    "gates_produced_bella",
    "gates_produced_rei",
    "gates_produced_savaria",
    "gates_produced_eleshi",
    "gates_produced_mornst",
    "gates_produced_maine",
    "gates_produced_garpa",
    "gates_produced_dmeacc",
    "gates_produced_admy",
    "gates_produced_other",
]

_GATE_PACKAGED_FIELDS = [
    "gates_packaged_ahe",
    "gates_packaged_bella",
    "gates_packaged_rei",
    "gates_packaged_savaria",
    "gates_packaged_eleshi",
    "gates_packaged_mornst",
    "gates_packaged_maine",
    "gates_packaged_garpa",
    "gates_packaged_dmeacc",
    "gates_packaged_admy",
    "gates_packaged_other",
]


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
