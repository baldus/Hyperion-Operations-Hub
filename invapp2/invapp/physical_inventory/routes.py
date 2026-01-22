"""Routes for physical inventory snapshots and reconciliation."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from invapp.auth import blueprint_page_guard
from invapp.extensions import db
from invapp.login import current_user
from invapp.models import (
    InventoryCountLine,
    InventorySnapshot,
    InventorySnapshotLine,
    Item,
    Location,
)
from invapp.superuser import superuser_required
from invapp.utils.csv_export import export_rows_to_csv

from .forms import parse_count_updates, parse_snapshot_upload_form
from .services import (
    REQUIRED_SNAPSHOT_HEADERS,
    build_count_sheet_rows,
    build_reconciliation_rows,
    ensure_count_lines_for_snapshot,
    parse_snapshot_csv,
    summarize_snapshot,
)

bp = Blueprint("physical_inventory", __name__, url_prefix="/physical-inventory")

bp.before_request(blueprint_page_guard("inventory"))


@bp.route("/snapshots")
@superuser_required
def list_snapshots():
    snapshots = (
        InventorySnapshot.query.order_by(InventorySnapshot.snapshot_date.desc()).all()
    )
    summary = {snapshot.id: summarize_snapshot(snapshot.id) for snapshot in snapshots}
    return render_template(
        "physical_inventory/snapshots.html",
        snapshots=snapshots,
        summary=summary,
    )


@bp.route("/snapshots/new", methods=["GET", "POST"])
@superuser_required
def create_snapshot():
    if request.method == "POST":
        payload, errors = parse_snapshot_upload_form(request.form, request.files)
        if errors:
            for message in errors:
                flash(message, "error")
            return render_template(
                "physical_inventory/snapshot_new.html",
                required_headers=REQUIRED_SNAPSHOT_HEADERS,
            )

        csv_bytes = payload.file.read()
        try:
            csv_text = csv_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("CSV must be encoded as UTF-8.", "error")
            return render_template(
                "physical_inventory/snapshot_new.html",
                required_headers=REQUIRED_SNAPSHOT_HEADERS,
            )

        header_overrides = {
            "item_code": (request.form.get("item_code_column") or "").strip(),
            "system_total_qty": (request.form.get("system_total_qty_column") or "").strip(),
            "uom": (request.form.get("uom_column") or "").strip(),
            "description": (request.form.get("description_column") or "").strip(),
            "notes": (request.form.get("notes_column") or "").strip(),
        }
        parsed_rows, parse_errors = parse_snapshot_csv(
            csv_text,
            header_overrides=header_overrides,
        )
        if parse_errors:
            for message in parse_errors:
                flash(message, "error")
            return render_template(
                "physical_inventory/snapshot_new.html",
                required_headers=REQUIRED_SNAPSHOT_HEADERS,
                uploaded_headers=list(csv.DictReader(io.StringIO(csv_text)).fieldnames or []),
                column_mapping=header_overrides,
            )

        snapshot = InventorySnapshot(
            name=payload.name,
            snapshot_date=payload.snapshot_date or datetime.utcnow(),
            source=payload.source or "CSV",
            created_by_user_id=current_user.id,
        )
        db.session.add(snapshot)
        db.session.flush()

        lines = [
            InventorySnapshotLine(
                snapshot_id=snapshot.id,
                item_id=row.item_id,
                system_total_qty=row.system_total_qty,
                uom=row.uom,
                notes=row.notes,
            )
            for row in parsed_rows
        ]
        db.session.add_all(lines)
        created_count_lines = ensure_count_lines_for_snapshot(snapshot)
        db.session.commit()

        flash(
            f"Snapshot created with {len(lines)} item totals and {created_count_lines} count lines.",
            "success",
        )
        return redirect(url_for("physical_inventory.view_snapshot", snapshot_id=snapshot.id))

    return render_template(
        "physical_inventory/snapshot_new.html",
        required_headers=REQUIRED_SNAPSHOT_HEADERS,
        uploaded_headers=[],
        column_mapping={},
    )


@bp.route("/snapshots/<int:snapshot_id>")
@superuser_required
def view_snapshot(snapshot_id: int):
    snapshot = InventorySnapshot.query.get_or_404(snapshot_id)
    summary = summarize_snapshot(snapshot.id)
    return render_template(
        "physical_inventory/snapshot_detail.html",
        snapshot=snapshot,
        summary=summary,
    )


@bp.route("/snapshots/<int:snapshot_id>/lock", methods=["POST"])
@superuser_required
def lock_snapshot(snapshot_id: int):
    snapshot = InventorySnapshot.query.get_or_404(snapshot_id)
    if snapshot.is_locked:
        flash("Snapshot is already locked.", "warning")
    else:
        snapshot.is_locked = True
        db.session.commit()
        flash("Snapshot locked. Counts are now read-only.", "success")
    return redirect(url_for("physical_inventory.view_snapshot", snapshot_id=snapshot_id))


@bp.route("/snapshots/<int:snapshot_id>/refresh-count-lines", methods=["POST"])
@superuser_required
def refresh_count_lines(snapshot_id: int):
    snapshot = InventorySnapshot.query.get_or_404(snapshot_id)
    added = ensure_count_lines_for_snapshot(snapshot)
    db.session.commit()
    flash(f"Added {added} missing count lines.", "success")
    return redirect(url_for("physical_inventory.view_snapshot", snapshot_id=snapshot_id))


@bp.route("/snapshots/<int:snapshot_id>/count", methods=["GET", "POST"])
@superuser_required
def count_snapshot(snapshot_id: int):
    snapshot = InventorySnapshot.query.get_or_404(snapshot_id)
    if snapshot.is_locked and request.method == "POST":
        flash("Snapshot is locked; counts cannot be edited.", "error")
        return redirect(
            url_for("physical_inventory.count_snapshot", snapshot_id=snapshot.id)
        )

    location_ids = (
        db.session.query(InventoryCountLine.location_id)
        .filter(InventoryCountLine.snapshot_id == snapshot.id)
        .distinct()
        .all()
    )
    location_id_list = [loc_id for loc_id, in location_ids]
    if location_id_list:
        locations = (
            Location.query.filter(Location.id.in_(location_id_list))
            .order_by(Location.code)
            .all()
        )
    else:
        locations = []

    selected_location_id = request.args.get("location_id", type=int)
    uncounted_only = request.args.get("uncounted", "0") == "1"

    if request.method == "POST":
        updates = parse_count_updates(request.form)
        errors: list[str] = []
        line_map = {
            line.id: line
            for line in InventoryCountLine.query.filter(
                InventoryCountLine.id.in_([u.line_id for u in updates]),
                InventoryCountLine.snapshot_id == snapshot.id,
            ).all()
        }

        for update in updates:
            line = line_map.get(update.line_id)
            if line is None:
                continue
            if update.counted_qty is None:
                line.counted_qty = None
                line.counted_by_user_id = None
                line.counted_at = None
            else:
                try:
                    qty = Decimal(update.counted_qty)
                except InvalidOperation:
                    errors.append(
                        f"Line {line.id} ({line.item.sku}): counted_qty must be numeric."
                    )
                    continue
                line.counted_qty = qty
                line.counted_by_user_id = current_user.id
                line.counted_at = datetime.utcnow()
            line.notes = update.notes

        if errors:
            db.session.rollback()
            for message in errors:
                flash(message, "error")
        else:
            db.session.commit()
            flash("Counts updated.", "success")

        return redirect(
            url_for(
                "physical_inventory.count_snapshot",
                snapshot_id=snapshot.id,
                location_id=request.args.get("location_id"),
                uncounted="1" if uncounted_only else "0",
            )
        )

    count_query = (
        InventoryCountLine.query.filter_by(snapshot_id=snapshot.id)
        .join(Item)
        .join(Location)
        .order_by(Item.sku)
    )

    if selected_location_id:
        count_query = count_query.filter(
            InventoryCountLine.location_id == selected_location_id
        )
    if uncounted_only:
        count_query = count_query.filter(InventoryCountLine.counted_qty.is_(None))

    count_lines = count_query.all()
    snapshot_lines = {
        line.item_id: line
        for line in InventorySnapshotLine.query.filter_by(snapshot_id=snapshot.id).all()
    }

    return render_template(
        "physical_inventory/count.html",
        snapshot=snapshot,
        locations=locations,
        selected_location_id=selected_location_id,
        uncounted_only=uncounted_only,
        count_lines=count_lines,
        snapshot_lines=snapshot_lines,
    )


@bp.route("/snapshots/<int:snapshot_id>/reconciliation")
@superuser_required
def reconciliation(snapshot_id: int):
    snapshot = InventorySnapshot.query.get_or_404(snapshot_id)
    rows = build_reconciliation_rows(snapshot.id)
    item_id = request.args.get("item_id", type=int)
    drilldown_lines = []
    if item_id:
        drilldown_lines = (
            InventoryCountLine.query.filter_by(snapshot_id=snapshot.id, item_id=item_id)
            .join(Location)
            .order_by(Location.code)
            .all()
        )
    return render_template(
        "physical_inventory/reconciliation.html",
        snapshot=snapshot,
        rows=rows,
        item_id=item_id,
        drilldown_lines=drilldown_lines,
    )


@bp.route("/snapshots/<int:snapshot_id>/export/location-sheet.csv")
@superuser_required
def export_location_sheet(snapshot_id: int):
    snapshot = InventorySnapshot.query.get_or_404(snapshot_id)
    rows = build_count_sheet_rows(snapshot.id)
    columns = (
        ("location_code", "location_code"),
        ("location_description", "location_description"),
        ("item_code", "item_code"),
        ("item_description", "item_description"),
        ("uom", "uom"),
        ("system_total_qty", "system_total_qty"),
        ("counted_qty", "counted_qty"),
        ("notes", "notes"),
    )
    filename = f"physical_inventory_location_sheet_{snapshot.id}.csv"
    return export_rows_to_csv(rows, columns, filename)


@bp.route("/snapshots/<int:snapshot_id>/export/reconciliation.csv")
@superuser_required
def export_reconciliation(snapshot_id: int):
    snapshot = InventorySnapshot.query.get_or_404(snapshot_id)
    rows = [row.__dict__ for row in build_reconciliation_rows(snapshot.id)]
    columns = (
        ("item_code", "item_code"),
        ("item_description", "item_description"),
        ("uom", "uom"),
        ("system_total_qty", "system_total_qty"),
        ("counted_total_qty", "counted_total_qty"),
        ("variance", "variance"),
        ("status", "status"),
    )
    filename = f"physical_inventory_reconciliation_{snapshot.id}.csv"
    return export_rows_to_csv(rows, columns, filename)
