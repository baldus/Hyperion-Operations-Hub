from datetime import datetime
import csv
from io import StringIO, BytesIO

import csv
from datetime import datetime
from io import BytesIO, StringIO

from flask import flash, redirect, render_template, request, send_file, url_for

from invapp.extensions import db
from invapp.mdi.models import CATEGORY_DISPLAY, CategoryMetric, MDIEntry, STATUS_BADGES


DEFAULT_STATUS_OPTIONS = ["Open", "In Progress", "Closed"]
CATEGORY_STATUS_OPTIONS = {
    "Safety": DEFAULT_STATUS_OPTIONS,
    "Quality": DEFAULT_STATUS_OPTIONS,
    "Delivery": DEFAULT_STATUS_OPTIONS,
    "People": [],
    "Materials": ["Open", "Reviewing", "Ordered", "Received", "Canceled"],
}

ENTRY_EXPORT_COLUMNS = [
    "ID",
    "Category",
    "Status",
    "Priority",
    "Owner",
    "Description",
    "Area",
    "Related Reference",
    "Notes",
    "Item Description",
    "Order Number",
    "Customer",
    "Due Date",
    "Number of Absentees",
    "Open Positions",
    "Item / Part Number",
    "Vendor",
    "ETA",
    "PO Number",
    "Metric Name",
    "Metric Value",
    "Metric Target",
    "Metric Unit",
    "Date Logged",
    "Created At",
    "Updated At",
]
def report_entry():
    entry_id = request.args.get("id")
    entry = MDIEntry.query.get(entry_id) if entry_id else None
    categories = list(CATEGORY_DISPLAY.keys())
    initial_category = entry.category if entry else (categories[0] if categories else "")
    previous_open_positions = None
    if entry and entry.category == "People" and entry.open_positions is not None:
        previous_open_positions = entry.open_positions
    else:
        previous_entry = _get_previous_people_entry(exclude_id=entry.id if entry else None)
        if previous_entry is not None:
            previous_open_positions = previous_entry.open_positions
    return render_template(
        "report_entry.html",
        entry=entry,
        category_meta=CATEGORY_DISPLAY,
        status_badges=STATUS_BADGES,
        category_status_options=CATEGORY_STATUS_OPTIONS,
        default_open_positions=previous_open_positions,
        initial_category=initial_category,
    )
def add_entry():
    entry = MDIEntry()
    _populate_entry_from_form(entry, request.form)
    if entry.category == "People" and entry.open_positions is None:
        previous_entry = _get_previous_people_entry()
        if previous_entry is not None:
            entry.open_positions = previous_entry.open_positions
    db.session.add(entry)
    db.session.commit()
    flash("MDI entry added successfully", "success")
    return redirect(url_for("mdi.meeting_view"))


def update_entry(entry_id):
    entry = MDIEntry.query.get_or_404(entry_id)
    _populate_entry_from_form(entry, request.form)
    entry.updated_at = datetime.utcnow()
    db.session.commit()
    flash("MDI entry updated", "info")
    return redirect(url_for("mdi.meeting_view"))


def delete_entry(entry_id):
    entry = MDIEntry.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    flash("MDI entry deleted", "warning")
    return redirect(url_for("mdi.meeting_view"))


def export_csv():
    entries = MDIEntry.query.order_by(MDIEntry.category, MDIEntry.priority.desc()).all()
    metrics = CategoryMetric.query.order_by(
        CategoryMetric.category, CategoryMetric.recorded_date, CategoryMetric.metric_name
    ).all()

    si = StringIO()
    writer = csv.writer(si)

    writer.writerow(["Entries"])
    writer.writerow(ENTRY_EXPORT_COLUMNS)
    for entry in entries:
        writer.writerow(
            [
                entry.id,
                entry.category,
                entry.status,
                entry.priority,
                entry.owner,
                entry.description or "",
                entry.area,
                entry.related_reference,
                entry.notes or "",
                entry.item_description or "",
                entry.order_number or "",
                entry.customer or "",
                entry.due_date.isoformat() if entry.due_date else "",
                entry.number_absentees if entry.number_absentees is not None else "",
                entry.open_positions if entry.open_positions is not None else "",
                entry.item_part_number or "",
                entry.vendor or "",
                entry.eta or "",
                entry.po_number or "",
                entry.metric_name or "",
                entry.metric_value if entry.metric_value is not None else "",
                entry.metric_target if entry.metric_target is not None else "",
                entry.metric_unit or "",
                entry.date_logged.isoformat() if entry.date_logged else "",
                entry.created_at.isoformat() if entry.created_at else "",
                entry.updated_at.isoformat() if entry.updated_at else "",
            ]
        )

    writer.writerow([])
    writer.writerow(["Category Metrics"])
    writer.writerow(
        [
            "ID",
            "Category",
            "Metric Name",
            "Dimension",
            "Value",
            "Target",
            "Unit",
            "Recorded Date",
            "Created At",
        ]
    )
    for metric in metrics:
        writer.writerow(
            [
                metric.id,
                metric.category,
                metric.metric_name,
                metric.dimension or "",
                metric.value,
                metric.target if metric.target is not None else "",
                metric.unit or "",
                metric.recorded_date.isoformat(),
                metric.created_at.isoformat() if metric.created_at else "",
            ]
        )

    output = BytesIO(si.getvalue().encode("utf-8"))
    output.seek(0)
    return send_file(
        output,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"mdi_data_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv",
    )
def import_csv():
    upload = request.files.get("file")
    if upload is None or upload.filename == "":
        flash("Please choose a CSV file to upload.", "warning")
        return redirect(url_for("mdi.meeting_view"))

    try:
        payload = upload.stream.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("Unable to decode the uploaded file. Please ensure it is UTF-8 encoded.", "danger")
        return redirect(url_for("mdi.meeting_view"))

    reader = csv.reader(StringIO(payload))

    entries_header = None
    metrics_header = None
    entries_rows = []
    metrics_rows = []
    section = None

    for row in reader:
        if not any(cell.strip() for cell in row):
            continue

        first_cell = row[0].strip().lower()
        if first_cell == "entries":
            section = "entries_header"
            continue
        if first_cell == "category metrics":
            section = "metrics_header"
            continue

        if section is None and entries_header is None:
            entries_header = [cell.strip() for cell in row]
            section = "entries"
            continue

        if section == "entries_header":
            entries_header = [cell.strip() for cell in row]
            section = "entries"
            continue
        if section == "metrics_header":
            metrics_header = [cell.strip() for cell in row]
            section = "metrics"
            continue

        if section == "entries":
            entries_rows.append(row)
        elif section == "metrics":
            metrics_rows.append(row)

    try:
        if entries_rows:
            db.session.query(MDIEntry).delete(synchronize_session=False)
            for row in entries_rows:
                entry_data = _map_row(entries_header, row)
                category = _clean_string(entry_data.get("Category"))
                if not category:
                    continue

                entry = MDIEntry(
                    category=category,
                    status=_clean_string(entry_data.get("Status")),
                    priority=_clean_string(entry_data.get("Priority")),
                    owner=_clean_string(entry_data.get("Owner")),
                    description=_clean_string(entry_data.get("Description")),
                    area=_clean_string(entry_data.get("Area")),
                    related_reference=_clean_string(entry_data.get("Related Reference")),
                    notes=_clean_string(entry_data.get("Notes")),
                    item_description=_clean_string(entry_data.get("Item Description")),
                    order_number=_clean_string(entry_data.get("Order Number")),
                    customer=_clean_string(entry_data.get("Customer")),
                    due_date=_parse_date(entry_data.get("Due Date")),
                    number_absentees=_parse_int(entry_data.get("Number of Absentees")),
                    open_positions=_parse_int(entry_data.get("Open Positions")),
                    item_part_number=_clean_string(entry_data.get("Item / Part Number")),
                    vendor=_clean_string(entry_data.get("Vendor")),
                    eta=_clean_string(entry_data.get("ETA")),
                    po_number=_clean_string(entry_data.get("PO Number")),
                    metric_name=_clean_string(entry_data.get("Metric Name")),
                    metric_value=_parse_float(entry_data.get("Metric Value")),
                    metric_target=_parse_float(entry_data.get("Metric Target")),
                    metric_unit=_clean_string(entry_data.get("Metric Unit")),
                    date_logged=_parse_date(entry_data.get("Date Logged")),
                )
                if entry.category == "Delivery" and not entry.description:
                    entry.description = entry.item_description or entry.notes or "Delivery update"
                elif entry.category == "People" and not entry.description:
                    entry.description = "People update"
                entry.created_at = _parse_datetime(entry_data.get("Created At"))
                entry.updated_at = _parse_datetime(entry_data.get("Updated At"))
                db.session.add(entry)

        if metrics_rows:
            db.session.query(CategoryMetric).delete(synchronize_session=False)
            for row in metrics_rows:
                metric_data = _map_row(metrics_header, row)
                category = metric_data.get("Category")
                metric_name = metric_data.get("Metric Name")
                if not category or not metric_name:
                    continue

                metric = CategoryMetric(
                    category=category,
                    metric_name=metric_name,
                    dimension=metric_data.get("Dimension"),
                    value=_parse_float(metric_data.get("Value")) or 0.0,
                    target=_parse_float(metric_data.get("Target")),
                    unit=metric_data.get("Unit"),
                    recorded_date=_parse_date(metric_data.get("Recorded Date")) or datetime.utcnow().date(),
                )
                metric.created_at = _parse_datetime(metric_data.get("Created At"))
                db.session.add(metric)

        if entries_rows or metrics_rows:
            db.session.commit()
            flash("CSV data imported successfully.", "success")
        else:
            flash("No data rows found in the uploaded CSV.", "warning")
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        flash(f"Failed to import CSV data: {exc}", "danger")

    return redirect(url_for("mdi.meeting_view"))


def _populate_entry_from_form(entry, form):
    entry.category = form.get("category")
    entry.status = _clean_string(form.get("status"))
    entry.priority = _clean_string(form.get("priority"))
    entry.owner = _clean_string(form.get("owner"))
    entry.area = _clean_string(form.get("area"))
    entry.description = _clean_string(form.get("description"))
    entry.related_reference = _clean_string(form.get("related_reference"))
    entry.notes = _clean_string(form.get("notes"))
    entry.item_description = _clean_string(form.get("item_description"))
    entry.order_number = _clean_string(form.get("order_number"))
    entry.customer = _clean_string(form.get("customer"))
    entry.due_date = _parse_date(form.get("due_date"))
    entry.number_absentees = _parse_int(form.get("number_absentees"))
    entry.open_positions = _parse_int(form.get("open_positions"))
    entry.item_part_number = _clean_string(form.get("item_part_number"))
    entry.vendor = _clean_string(form.get("vendor"))
    entry.eta = _clean_string(form.get("eta"))
    entry.po_number = _clean_string(form.get("po_number"))
    entry.date_logged = _parse_date(form.get("date_logged"))
    entry.metric_name = None
    entry.metric_value = None
    entry.metric_target = None
    entry.metric_unit = None
    if entry.category == "Delivery":
        if not entry.description:
            entry.description = entry.item_description or entry.notes or "Delivery update"
    elif entry.category == "People":
        if not entry.description:
            entry.description = "People update"


def _get_previous_people_entry(exclude_id=None):
    query = MDIEntry.query.filter(MDIEntry.category == "People")
    if exclude_id is not None:
        query = query.filter(MDIEntry.id != exclude_id)
    return query.order_by(MDIEntry.date_logged.desc(), MDIEntry.created_at.desc()).first()


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


def register(bp):
    bp.add_url_rule("/mdi/report", view_func=report_entry, methods=["GET"], endpoint="report_entry")
    bp.add_url_rule("/mdi/report/add", view_func=add_entry, methods=["POST"], endpoint="report_add_entry")
    bp.add_url_rule(
        "/mdi/report/update/<int:entry_id>",
        view_func=update_entry,
        methods=["POST"],
        endpoint="report_update_entry",
    )
    bp.add_url_rule(
        "/mdi/report/delete/<int:entry_id>",
        view_func=delete_entry,
        methods=["POST"],
        endpoint="report_delete_entry",
    )
    bp.add_url_rule(
        "/mdi/report/export/csv",
        view_func=export_csv,
        methods=["GET"],
        endpoint="report_export_csv",
    )
    bp.add_url_rule(
        "/mdi/report/import/csv",
        view_func=import_csv,
        methods=["POST"],
        endpoint="report_import_csv",
    )


def _clean_string(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _parse_datetime(value):
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _map_row(header, row):
    mapping = {}
    if not header:
        return mapping
    for index, column in enumerate(header):
        mapping[column] = row[index].strip() if index < len(row) else ""
    return mapping

