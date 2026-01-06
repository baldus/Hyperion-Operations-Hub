from datetime import datetime, date, timedelta
import random

from sqlalchemy import inspect, text

from invapp.extensions import db


class MDIEntry(db.Model):
    __tablename__ = "mdi_entries"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    owner = db.Column(db.String(100))
    status = db.Column(db.String(20), default="Open")
    priority = db.Column(db.String(20))
    area = db.Column(db.String(100))
    related_reference = db.Column(db.String(100))
    notes = db.Column(db.Text)
    item_description = db.Column(db.String(255))
    order_number = db.Column(db.String(100))
    customer = db.Column(db.String(120))
    due_date = db.Column(db.Date)
    number_absentees = db.Column(db.Integer)
    open_positions = db.Column(db.Integer)
    item_part_number = db.Column(db.String(120))
    vendor = db.Column(db.String(120))
    eta = db.Column(db.String(120))
    po_number = db.Column(db.String(120))
    metric_name = db.Column(db.String(100))
    metric_value = db.Column(db.Float)
    metric_target = db.Column(db.Float)
    metric_unit = db.Column(db.String(50))
    date_logged = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "owner": self.owner,
            "status": self.status,
            "priority": self.priority,
            "area": self.area,
            "related_reference": self.related_reference,
            "notes": self.notes,
            "item_description": self.item_description,
            "order_number": self.order_number,
            "customer": self.customer,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "number_absentees": self.number_absentees,
            "open_positions": self.open_positions,
            "item_part_number": self.item_part_number,
            "vendor": self.vendor,
            "eta": self.eta,
            "po_number": self.po_number,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "metric_target": self.metric_target,
            "metric_unit": self.metric_unit,
            "date_logged": self.date_logged.isoformat() if self.date_logged else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CategoryMetric(db.Model):
    __tablename__ = "mdi_category_metrics"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False, index=True)
    metric_name = db.Column(db.String(100), nullable=False)
    dimension = db.Column(db.String(100))
    value = db.Column(db.Float, nullable=False)
    target = db.Column(db.Float)
    unit = db.Column(db.String(50))
    recorded_date = db.Column(db.Date, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "metric_name": self.metric_name,
            "dimension": self.dimension,
            "value": self.value,
            "target": self.target,
            "unit": self.unit,
            "recorded_date": self.recorded_date.isoformat(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


CATEGORY_DISPLAY = {
    "Safety": {"color": "danger", "icon": "bi-shield-fill"},
    "Quality": {"color": "primary", "icon": "bi-clipboard-check"},
    "Delivery": {"color": "warning", "icon": "bi-truck"},
    "People": {"color": "success", "icon": "bi-people-fill"},
    "Materials": {"color": "info", "icon": "bi-box-seam"},
}


STATUS_BADGES = {
    "Open": "secondary",
    "In Progress": "warning",
    "Closed": "success",
}


def seed_data():
    entries_seeded = MDIEntry.query.first() is not None
    metrics_seeded = CategoryMetric.query.first() is not None

    if entries_seeded and metrics_seeded:
        return

    rng = random.Random(42)
    today = date.today()
    statuses = ["Open", "In Progress", "Closed"]
    priorities = ["High", "Medium", "Low"]
    created_records = False

    if not entries_seeded:
        category_configs = {
            "Safety": {
                "owners": ["Alex Morgan", "Taylor Reed", "Morgan Blake"],
                "areas": ["Assembly", "Paint", "Logistics"],
                "descriptions": [
                    "Review incident follow-up in {area}",
                    "Conduct safety observation walk in {area}",
                    "Update LOTO signage for {area}",
                    "Verify PPE compliance for {area}",
                ],
            },
            "Quality": {
                "owners": ["Jamie Chen", "Priya Singh", "Jordan Miles"],
                "areas": ["Line A", "Line B", "Inspection"],
                "descriptions": [
                    "Investigate defect trend on {area}",
                    "Audit quality checks for {area}",
                    "Capture observation results from {area}",
                    "Review supplier notice impacting {area}",
                ],
                "references": ["CAR", "SCAR", "NCMR", "Audit"],
            },
            "Delivery": {
                "owners": ["Avery Brooks", "Skyler James", "Reese Nolan"],
                "areas": ["Line A", "Line B", "Final Pack"],
                "notes": [
                    "Coordinate expedited shipment for {area}",
                    "Balance staffing for {area} takt time",
                    "Track backlog clearance in {area}",
                    "Confirm carrier availability for {area}",
                ],
                "items": ["Motor Housing", "Control Panel", "Gear Assembly", "Bearing Kit"],
                "customers": ["Apex Industries", "Brightline", "Summit Works", "Northern Corp"],
            },
            "People": {
                "areas": ["Gates", "Electronics"],
                "absentees": range(0, 6),
                "open_positions": range(0, 4),
            },
        }

        for category, config in category_configs.items():
            for _ in range(rng.randint(5, 10)):
                entry_date = today - timedelta(days=rng.randint(0, 13))
                entry_kwargs = {
                    "category": category,
                    "status": rng.choice(statuses),
                    "priority": rng.choice(priorities),
                    "date_logged": entry_date,
                }

                if category == "Safety":
                    area = rng.choice(config["areas"])
                    entry_kwargs.update(
                        description=rng.choice(config["descriptions"]).format(area=area),
                        owner=rng.choice(config["owners"]),
                        area=area,
                    )
                elif category == "Quality":
                    area = rng.choice(config["areas"])
                    entry_kwargs.update(
                        description=rng.choice(config["descriptions"]).format(area=area),
                        owner=rng.choice(config["owners"]),
                        area=area,
                        related_reference=f"{rng.choice(config['references'])}-{rng.randint(100, 999)}",
                    )
                elif category == "Delivery":
                    area = rng.choice(config["areas"])
                    item_desc = rng.choice(config["items"])
                    entry_kwargs.update(
                        description=f"Delivery update for {item_desc}",
                        notes=rng.choice(config["notes"]).format(area=area),
                        item_description=item_desc,
                        owner=rng.choice(config["owners"]),
                        area=area,
                        order_number=f"ORD-{rng.randint(1000, 9999)}",
                        customer=rng.choice(config["customers"]),
                        due_date=entry_date + timedelta(days=rng.randint(1, 10)),
                    )
                elif category == "People":
                    entry_kwargs.update(
                        description=f"People update for {entry_date.strftime('%Y-%m-%d')}",
                        number_absentees=rng.choice(list(config["absentees"])),
                        open_positions=rng.choice(list(config["open_positions"])),
                    )
                elif category == "Materials":
                    area = rng.choice(config["areas"])
                    entry_kwargs.update(
                        description=rng.choice(config["descriptions"]).format(area=area),
                        owner=rng.choice(config["owners"]),
                        area=area,
                        item_part_number=rng.choice(config["items"]),
                        vendor=rng.choice(config["vendors"]),
                        eta=(entry_date + timedelta(days=rng.randint(2, 14))).strftime("%Y-%m-%d"),
                        po_number=f"PO-{rng.randint(5000, 9999)}",
                    )

                entry = MDIEntry(**entry_kwargs)
                db.session.add(entry)
        created_records = True

    if not metrics_seeded:
        for offset in range(13, -1, -1):
            recorded_date = today - timedelta(days=offset)

            incidents = rng.randint(0, 2)
            observations = rng.randint(1, 6)
            notices = rng.randint(0, 4)
            quality_observations = rng.randint(1, 5)
            production_output = rng.randint(180, 260)
            gates_attendance = rng.randint(35, 70)
            electronics_attendance = rng.randint(30, 65)

            db.session.add(
                CategoryMetric(
                    category="Safety",
                    metric_name="Incidents",
                    value=float(incidents),
                    unit=None,
                    recorded_date=recorded_date,
                )
            )
            db.session.add(
                CategoryMetric(
                    category="Safety",
                    metric_name="Observations",
                    value=float(observations),
                    unit=None,
                    recorded_date=recorded_date,
                )
            )
            db.session.add(
                CategoryMetric(
                    category="Quality",
                    metric_name="Notices",
                    value=float(notices),
                    unit=None,
                    recorded_date=recorded_date,
                )
            )
            db.session.add(
                CategoryMetric(
                    category="Quality",
                    metric_name="Observations",
                    value=float(quality_observations),
                    unit=None,
                    recorded_date=recorded_date,
                )
            )
            db.session.add(
                CategoryMetric(
                    category="Delivery",
                    metric_name="Production Output",
                    value=float(production_output),
                    target=240.0,
                    unit="units",
                    recorded_date=recorded_date,
                )
            )
            db.session.add(
                CategoryMetric(
                    category="People",
                    metric_name="Attendance",
                    dimension="Gates",
                    value=float(gates_attendance),
                    unit="employees",
                    recorded_date=recorded_date,
                )
            )
            db.session.add(
                CategoryMetric(
                    category="People",
                    metric_name="Attendance",
                    dimension="Electronics",
                    value=float(electronics_attendance),
                    unit="employees",
                    recorded_date=recorded_date,
                )
            )

        created_records = True

    if created_records:
        db.session.commit()


def _seed_metric_name(category: str, rng: random.Random) -> str:
    mapping = {
        "Safety": ["Recordable Incidents", "Safety Observations"],
        "Quality": ["Defect Rate", "Audit Findings"],
        "Delivery": ["Daily Output", "Schedule Adherence"],
        "People": ["Attendance", "Training Completion"],
        "Materials": ["Open Shortages", "Material Turns"],
    }
    return rng.choice(mapping.get(category, ["Metric"]))


def _seed_metric_value(category: str, rng: random.Random) -> float:
    if category == "Safety":
        return float(rng.randint(0, 3))
    if category == "Quality":
        return round(rng.uniform(1.5, 5.5), 2)
    if category == "Delivery":
        return float(rng.randint(180, 260))
    if category == "People":
        return float(rng.randint(35, 80))
    if category == "Materials":
        return float(rng.randint(1, 12))
    return 0.0


def _seed_metric_target(category: str) -> float:
    targets = {
        "Safety": 1.0,
        "Quality": 3.0,
        "Delivery": 240.0,
        "People": 75.0,
        "Materials": 5.0,
    }
    return targets.get(category, 0.0)


def _seed_metric_unit(category: str) -> str:
    units = {
        "Safety": "cases",
        "Quality": "%",
        "Delivery": "units",
        "People": "employees",
        "Materials": "items",
    }
    return units.get(category, "")


def ensure_schema():
    """Ensure legacy databases receive new optional metric columns."""

    engine = db.get_engine()
    inspector = inspect(engine)

    existing_tables = inspector.get_table_names()
    if CategoryMetric.__tablename__ not in existing_tables:
        CategoryMetric.__table__.create(bind=engine, checkfirst=True)

    try:
        existing_columns = {column["name"] for column in inspector.get_columns(MDIEntry.__tablename__)}
    except Exception:
        # Table has not been created yet; create_all will handle it later.
        return

    columns_to_add = {
        "notes": "ALTER TABLE mdi_entries ADD COLUMN notes TEXT",
        "item_description": "ALTER TABLE mdi_entries ADD COLUMN item_description VARCHAR(255)",
        "order_number": "ALTER TABLE mdi_entries ADD COLUMN order_number VARCHAR(100)",
        "customer": "ALTER TABLE mdi_entries ADD COLUMN customer VARCHAR(120)",
        "due_date": "ALTER TABLE mdi_entries ADD COLUMN due_date DATE",
        "number_absentees": "ALTER TABLE mdi_entries ADD COLUMN number_absentees INTEGER",
        "open_positions": "ALTER TABLE mdi_entries ADD COLUMN open_positions INTEGER",
        "item_part_number": "ALTER TABLE mdi_entries ADD COLUMN item_part_number VARCHAR(120)",
        "vendor": "ALTER TABLE mdi_entries ADD COLUMN vendor VARCHAR(120)",
        "eta": "ALTER TABLE mdi_entries ADD COLUMN eta VARCHAR(120)",
        "po_number": "ALTER TABLE mdi_entries ADD COLUMN po_number VARCHAR(120)",
        "metric_name": "ALTER TABLE mdi_entries ADD COLUMN metric_name VARCHAR(100)",
        "metric_value": "ALTER TABLE mdi_entries ADD COLUMN metric_value FLOAT",
        "metric_target": "ALTER TABLE mdi_entries ADD COLUMN metric_target FLOAT",
        "metric_unit": "ALTER TABLE mdi_entries ADD COLUMN metric_unit VARCHAR(50)",
    }

    with engine.begin() as connection:
        for column_name, ddl in columns_to_add.items():
            if column_name not in existing_columns:
                connection.execute(text(ddl))
