from datetime import datetime

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
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"

    ACTIVE_STATES = {OPEN}


class Order(db.Model):
    __tablename__ = "order"

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String, unique=True, nullable=False)
    status = db.Column(db.String, nullable=False, default=OrderStatus.OPEN)
    promised_date = db.Column(db.Date, nullable=True)
    scheduled_start_date = db.Column(db.Date, nullable=True)
    scheduled_completion_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    items = db.relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.id",
    )
    steps = db.relationship(
        "OrderStep",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderStep.sequence",
    )

    def __repr__(self):
        return f"<Order {self.order_number} status={self.status}>"

    @property
    def primary_item(self):
        return self.items[0] if self.items else None

    @property
    def routing_progress(self):
        if not self.steps:
            return None
        completed = sum(1 for step in self.steps if step.completed)
        total = len(self.steps)
        if total == 0:
            return None
        return completed / total


class OrderItem(db.Model):
    __tablename__ = "order_item"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    promised_date = db.Column(db.Date, nullable=True)
    scheduled_start_date = db.Column(db.Date, nullable=True)
    scheduled_completion_date = db.Column(db.Date, nullable=True)

    order = db.relationship("Order", back_populates="items")
    item = db.relationship("Item")
    bom_components = db.relationship(
        "OrderBOMComponent",
        back_populates="order_item",
        cascade="all, delete-orphan",
        order_by="OrderBOMComponent.id",
    )
    reservations = db.relationship(
        "Reservation",
        back_populates="order_item",
        cascade="all, delete-orphan",
        order_by="Reservation.id",
    )

    def __repr__(self):
        return f"<OrderItem order={self.order_id} item={self.item_id} qty={self.quantity}>"


class OrderBOMComponent(db.Model):
    __tablename__ = "order_bom_component"

    id = db.Column(db.Integer, primary_key=True)
    order_item_id = db.Column(db.Integer, db.ForeignKey("order_item.id"), nullable=False)
    component_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    order_item = db.relationship("OrderItem", back_populates="bom_components")
    component_item = db.relationship("Item")
    step_usages = db.relationship(
        "OrderStepComponent",
        back_populates="bom_component",
        cascade="all, delete-orphan",
        order_by="OrderStepComponent.id",
    )

    def __repr__(self):
        return (
            f"<OrderBOMComponent order_item={self.order_item_id} "
            f"component={self.component_item_id} qty={self.quantity}>"
        )


class OrderStep(db.Model):
    __tablename__ = "order_step"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    sequence = db.Column(db.Integer, nullable=False)
    work_cell = db.Column(db.String, nullable=True)
    description = db.Column(db.String, nullable=False)
    completed = db.Column(db.Boolean, default=False, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    order = db.relationship("Order", back_populates="steps")
    component_usages = db.relationship(
        "OrderStepComponent",
        back_populates="order_step",
        cascade="all, delete-orphan",
        order_by="OrderStepComponent.id",
    )

    def __repr__(self):
        return (
            f"<OrderStep order={self.order_id} seq={self.sequence} "
            f"completed={self.completed}>"
        )


class Reservation(db.Model):
    __tablename__ = "reservation"

    id = db.Column(db.Integer, primary_key=True)
    order_item_id = db.Column(db.Integer, db.ForeignKey("order_item.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    order_item = db.relationship("OrderItem", back_populates="reservations")
    item = db.relationship("Item")

    def __repr__(self):
        return (
            f"<Reservation order_item={self.order_item_id} item={self.item_id} "
            f"qty={self.quantity}>"
        )


class OrderStepComponent(db.Model):
    __tablename__ = "order_step_component"

    id = db.Column(db.Integer, primary_key=True)
    order_step_id = db.Column(db.Integer, db.ForeignKey("order_step.id"), nullable=False)
    order_bom_component_id = db.Column(
        db.Integer, db.ForeignKey("order_bom_component.id"), nullable=False
    )

    order_step = db.relationship("OrderStep", back_populates="component_usages")
    bom_component = db.relationship("OrderBOMComponent", back_populates="step_usages")

    def __repr__(self):
        return (
            f"<OrderStepComponent step={self.order_step_id} "
            f"bom_component={self.order_bom_component_id}>"
        )
