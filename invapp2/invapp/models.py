from datetime import datetime
from invapp.extensions import db

class Item(db.Model):
    __tablename__ = "item"
    id = db.Column(db.Integer, primary_key=True)  # system key
    sku = db.Column(db.String, unique=True, nullable=False)  # part number
    name = db.Column(db.String, nullable=False)
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


class Order(db.Model):
    __tablename__ = "order"
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity_required = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String, default="PLANNED")
    due_date = db.Column(db.DateTime, nullable=True)

    item = db.relationship("Item", backref="orders")


class BillOfMaterial(db.Model):
    __tablename__ = "bill_of_material"
    id = db.Column(db.Integer, primary_key=True)
    parent_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    component_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)

    parent_item = db.relationship("Item", foreign_keys=[parent_item_id], backref="bom_parents")
    component_item = db.relationship("Item", foreign_keys=[component_item_id], backref="bom_components")


class RoutingStep(db.Model):
    __tablename__ = "routing_step"
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    sequence = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String, nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=True)

    item = db.relationship("Item", backref="routing_steps")
    location = db.relationship("Location", backref="routing_steps")


class OrderStep(db.Model):
    __tablename__ = "order_step"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    routing_step_id = db.Column(db.Integer, db.ForeignKey("routing_step.id"), nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    order = db.relationship("Order", backref="order_steps")
    routing_step = db.relationship("RoutingStep", backref="order_steps")


class Reservation(db.Model):
    __tablename__ = "reservation"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey("batch.id"), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=True)
    quantity = db.Column(db.Integer, nullable=False)

    order = db.relationship("Order", backref="reservations")
    item = db.relationship("Item", backref="reservations")
    batch = db.relationship("Batch", backref="reservations")
    location = db.relationship("Location", backref="reservations")
