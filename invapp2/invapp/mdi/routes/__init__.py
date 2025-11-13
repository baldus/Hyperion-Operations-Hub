from datetime import datetime

from flask import Blueprint, jsonify, request

from . import meeting, api, report  # noqa

from models.mdi_models import MDIEntry, db


api_bp = Blueprint("api", __name__)


@api_bp.get("/mdi_entries")
def get_entries():
    category = request.args.get("category")
    status = request.args.get("status")
    date = request.args.get("date")

    query = MDIEntry.query
    if category:
        query = query.filter(MDIEntry.category == category)
    if status:
        query = query.filter(MDIEntry.status == status)
    if date:
        try:
            query = query.filter(MDIEntry.date_logged == datetime.strptime(date, "%Y-%m-%d").date())
        except ValueError:
            pass

    entries = query.order_by(MDIEntry.created_at.desc()).all()
    return jsonify([entry.to_dict() for entry in entries])


@api_bp.post("/mdi_entries")
def create_entry():
    data = request.get_json(force=True)
    entry = MDIEntry(
        category=data.get("category"),
        description=data.get("description"),
        owner=data.get("owner"),
        status=data.get("status", "Open"),
        priority=data.get("priority"),
        area=data.get("area"),
        related_reference=data.get("related_reference"),
        notes=data.get("notes"),
        item_description=data.get("item_description"),
        order_number=data.get("order_number"),
        customer=data.get("customer"),
        due_date=_parse_date(data.get("due_date")),
        number_absentees=_parse_int(data.get("number_absentees")),
        open_positions=_parse_int(data.get("open_positions")),
        item_part_number=data.get("item_part_number"),
        vendor=data.get("vendor"),
        eta=data.get("eta"),
        po_number=data.get("po_number"),
        metric_name=data.get("metric_name"),
        metric_value=_parse_float(data.get("metric_value")),
        metric_target=_parse_float(data.get("metric_target")),
        metric_unit=data.get("metric_unit"),
        date_logged=_parse_date(data.get("date_logged")),
    )
    if entry.category == "Delivery" and not entry.description:
        entry.description = entry.item_description or entry.notes or "Delivery update"
    elif entry.category == "People" and not entry.description:
        entry.description = "People update"
    db.session.add(entry)
    db.session.commit()
    return jsonify(entry.to_dict()), 201


@api_bp.put("/mdi_entries/<int:entry_id>")
def update_entry(entry_id):
    entry = MDIEntry.query.get_or_404(entry_id)
    data = request.get_json(force=True)

    for field in [
        "category",
        "description",
        "owner",
        "status",
        "priority",
        "area",
        "related_reference",
        "notes",
        "item_description",
        "order_number",
        "customer",
        "item_part_number",
        "vendor",
        "eta",
        "po_number",
        "metric_name",
        "metric_unit",
    ]:
        if field in data:
            setattr(entry, field, data[field])

    if "date_logged" in data:
        entry.date_logged = _parse_date(data.get("date_logged"))
    if "due_date" in data:
        entry.due_date = _parse_date(data.get("due_date"))
    if "metric_value" in data:
        entry.metric_value = _parse_float(data.get("metric_value"))
    if "metric_target" in data:
        entry.metric_target = _parse_float(data.get("metric_target"))
    if "number_absentees" in data:
        entry.number_absentees = _parse_int(data.get("number_absentees"))
    if "open_positions" in data:
        entry.open_positions = _parse_int(data.get("open_positions"))

    if entry.category == "Delivery" and not entry.description:
        entry.description = entry.item_description or entry.notes or "Delivery update"
    elif entry.category == "People" and not entry.description:
        entry.description = "People update"

    entry.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(entry.to_dict())


@api_bp.delete("/mdi_entries/<int:entry_id>")
def delete_entry(entry_id):
    entry = MDIEntry.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    return "", 204


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

