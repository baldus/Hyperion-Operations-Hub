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
