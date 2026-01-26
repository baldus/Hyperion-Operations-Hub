"""Routes for physical inventory snapshots and reconciliation."""

from __future__ import annotations

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
    build_preview_rows,
    build_reconciliation_rows,
    delete_import_payload,
    ensure_count_lines_for_snapshot,
    get_item_field_candidates,
    group_duplicate_rows,
    load_import_payload,
    match_items,
    parse_import_file,
    store_import_payload,
    suggest_description_column,
    suggest_part_number_column,
    suggest_quantity_column,
    summarize_snapshot,
    resolve_item_description,
    resolve_item_part_number,
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
    candidates = get_item_field_candidates()
    if request.method == "POST":
        step = request.form.get("step", "upload")
        if step == "upload":
            payload, errors = parse_snapshot_upload_form(request.form, request.files)
            if errors:
                for message in errors:
                    flash(message, "error")
                return render_template(
                    "physical_inventory/snapshot_upload.html",
                    required_headers=REQUIRED_SNAPSHOT_HEADERS,
                    candidates=candidates,
                )

            if not candidates.part_number_fields:
                flash(
                    "No part number fields are configured for Item matching. "
                    "Set PHYS_INV_ITEM_ID_FIELDS in config.",
                    "error",
                )
                return render_template(
                    "physical_inventory/snapshot_upload.html",
                    required_headers=REQUIRED_SNAPSHOT_HEADERS,
                    candidates=candidates,
                )

            try:
                parsed = parse_import_file(payload.file.filename, io.BytesIO(payload.file.read()))
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template(
                    "physical_inventory/snapshot_upload.html",
                    required_headers=REQUIRED_SNAPSHOT_HEADERS,
                    candidates=candidates,
                )

            import_token = store_import_payload(
                "physical_inventory",
                {
                    "headers": parsed.headers,
                    "normalized_headers": parsed.normalized_headers,
                    "rows": parsed.rows,
                },
            )

            quantity_suggestion = suggest_quantity_column(
                parsed.normalized_headers, parsed.rows
            )
            part_suggestion = suggest_part_number_column(
                parsed.normalized_headers, parsed.rows, candidates
            )
            desc_suggestion = suggest_description_column(
                parsed.normalized_headers, parsed.rows
            )

            return render_template(
                "physical_inventory/snapshot_mapping.html",
                import_token=import_token,
                headers=parsed.normalized_headers,
                preview_rows=parsed.rows[:50],
                required_headers=REQUIRED_SNAPSHOT_HEADERS,
                candidates=candidates,
                snapshot_name=payload.name,
                snapshot_date=(payload.snapshot_date or datetime.utcnow()).strftime("%Y-%m-%d"),
                snapshot_source=payload.source or payload.file.filename,
                selected_mapping={
                    "part_number": part_suggestion,
                    "quantity": quantity_suggestion,
                    "description": desc_suggestion,
                },
                duplicate_strategy="sum",
            )

        if step == "mapping":
            import_token = request.form.get("import_token", "")
            payload = load_import_payload("physical_inventory", import_token)
            if not payload:
                flash("Import session expired. Please upload the file again.", "error")
                return redirect(url_for("physical_inventory.create_snapshot"))

            headers = payload.get("normalized_headers", [])
            rows = payload.get("rows", [])
            part_col = (request.form.get("part_number_column") or "").strip()
            quantity_col = (request.form.get("quantity_column") or "").strip()
            desc_col = (request.form.get("description_column") or "").strip() or None
            uom_col = (request.form.get("uom_column") or "").strip() or None
            notes_col = (request.form.get("notes_column") or "").strip() or None
            duplicate_strategy = request.form.get("duplicate_strategy", "sum")

            mapping_errors = []
            if not part_col or part_col not in headers:
                mapping_errors.append("Select a valid Part Number column.")
            if not quantity_col or quantity_col not in headers:
                mapping_errors.append("Select a valid Quantity column.")
            for optional_col in (desc_col, uom_col, notes_col):
                if optional_col and optional_col not in headers:
                    mapping_errors.append(
                        f"Column '{optional_col}' does not exist in the uploaded file."
                    )

            if mapping_errors:
                for message in mapping_errors:
                    flash(message, "error")
                return render_template(
                    "physical_inventory/snapshot_mapping.html",
                    import_token=import_token,
                    headers=headers,
                    preview_rows=rows[:50],
                    required_headers=REQUIRED_SNAPSHOT_HEADERS,
                    candidates=candidates,
                    snapshot_name=request.form.get("snapshot_name"),
                    snapshot_date=request.form.get("snapshot_date"),
                    snapshot_source=request.form.get("snapshot_source"),
                    selected_mapping={
                        "part_number": part_col,
                        "quantity": quantity_col,
                        "description": desc_col,
                        "uom": uom_col,
                        "notes": notes_col,
                    },
                    duplicate_strategy=duplicate_strategy,
                )

            grouped_rows, duplicate_summary = group_duplicate_rows(
                rows,
                part_col=part_col,
                desc_col=desc_col,
                quantity_col=quantity_col,
                strategy=duplicate_strategy,
            )
            match_context = match_items(grouped_rows, part_col, desc_col, candidates)
            preview_rows = build_preview_rows(
                grouped_rows,
                match_context.matches,
                part_col,
                desc_col,
                quantity_col,
            )
            unmatched_preview = [
                row for row in preview_rows if row.match_reason == "unmatched"
            ][:50]

            return render_template(
                "physical_inventory/snapshot_review.html",
                import_token=import_token,
                stats={
                    "total_rows": len(grouped_rows),
                    "matched_rows": match_context.summary.matched_rows,
                    "unmatched_rows": match_context.summary.unmatched_rows,
                    "collisions_resolved": match_context.summary.part_desc_matches,
                    "duplicate_groups": duplicate_summary.duplicate_groups,
                },
                unmatched_rows=unmatched_preview,
                snapshot_name=request.form.get("snapshot_name"),
                snapshot_date=request.form.get("snapshot_date"),
                snapshot_source=request.form.get("snapshot_source"),
                mapping={
                    "part_number": part_col,
                    "quantity": quantity_col,
                    "description": desc_col,
                    "uom": uom_col,
                    "notes": notes_col,
                },
                duplicate_strategy=duplicate_strategy,
            )

        if step == "commit":
            import_token = request.form.get("import_token", "")
            payload = load_import_payload("physical_inventory", import_token)
            if not payload:
                flash("Import session expired. Please upload the file again.", "error")
                return redirect(url_for("physical_inventory.create_snapshot"))

            headers = payload.get("normalized_headers", [])
            rows = payload.get("rows", [])
            part_col = request.form.get("part_number_column")
            quantity_col = request.form.get("quantity_column")
            desc_col = request.form.get("description_column") or None
            uom_col = request.form.get("uom_column") or None
            notes_col = request.form.get("notes_column") or None
            duplicate_strategy = request.form.get("duplicate_strategy", "sum")

            if not part_col or part_col not in headers or not quantity_col or quantity_col not in headers:
                flash("Mapping information is incomplete. Please retry the upload.", "error")
                return redirect(url_for("physical_inventory.create_snapshot"))

            grouped_rows, _ = group_duplicate_rows(
                rows,
                part_col=part_col,
                desc_col=desc_col,
                quantity_col=quantity_col,
                strategy=duplicate_strategy,
            )
            match_context = match_items(grouped_rows, part_col, desc_col, candidates)

            if match_context.summary.matched_rows == 0:
                flash("No rows matched existing items. Review your mapping.", "error")
                return redirect(url_for("physical_inventory.create_snapshot"))

            snapshot_date = request.form.get("snapshot_date") or ""
            parsed_date = None
            if snapshot_date:
                try:
                    parsed_date = datetime.strptime(snapshot_date, "%Y-%m-%d")
                except ValueError:
                    parsed_date = datetime.utcnow()

            snapshot = InventorySnapshot(
                name=request.form.get("snapshot_name") or None,
                snapshot_date=parsed_date or datetime.utcnow(),
                source=request.form.get("snapshot_source") or "Import",
                created_by_user_id=current_user.id,
            )
            db.session.add(snapshot)
            db.session.flush()

            lines = []
            for row, match in zip(grouped_rows, match_context.matches):
                if not match.item_id:
                    continue
                raw_qty = row.get(quantity_col, "")
                try:
                    qty = Decimal(str(raw_qty).strip())
                except InvalidOperation:
                    flash(
                        f"Invalid quantity '{raw_qty}' in import file. Fix and retry.",
                        "error",
                    )
                    db.session.rollback()
                    return redirect(url_for("physical_inventory.create_snapshot"))

                lines.append(
                    InventorySnapshotLine(
                        snapshot_id=snapshot.id,
                        item_id=match.item_id,
                        system_total_qty=qty,
                        uom=row.get(uom_col) if uom_col else None,
                        notes=row.get(notes_col) if notes_col else None,
                        source_part_number_text=row.get(part_col),
                        source_description_text=row.get(desc_col) if desc_col else None,
                    )
                )

            db.session.add_all(lines)
            created_count_lines = ensure_count_lines_for_snapshot(snapshot)
            db.session.commit()
            delete_import_payload("physical_inventory", import_token)

            flash(
                f"Snapshot created with {len(lines)} item totals and {created_count_lines} count lines.",
                "success",
            )
            return redirect(
                url_for("physical_inventory.view_snapshot", snapshot_id=snapshot.id)
            )

    return render_template(
        "physical_inventory/snapshot_upload.html",
        required_headers=REQUIRED_SNAPSHOT_HEADERS,
        candidates=candidates,
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
    candidates = get_item_field_candidates()
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
    part_number_map = {
        line.item_id: resolve_item_part_number(line.item, candidates)
        for line in count_lines
    }
    description_map = {
        line.item_id: resolve_item_description(line.item, candidates)
        for line in count_lines
    }

    return render_template(
        "physical_inventory/count.html",
        snapshot=snapshot,
        locations=locations,
        selected_location_id=selected_location_id,
        uncounted_only=uncounted_only,
        count_lines=count_lines,
        snapshot_lines=snapshot_lines,
        part_number_map=part_number_map,
        description_map=description_map,
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
        ("part_number", "part_number"),
        ("description", "description"),
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
        ("part_number", "part_number"),
        ("description", "description"),
        ("uom", "uom"),
        ("system_total_qty", "system_total_qty"),
        ("counted_total_qty", "counted_total_qty"),
        ("variance", "variance"),
        ("status", "status"),
    )
    filename = f"physical_inventory_reconciliation_{snapshot.id}.csv"
    return export_rows_to_csv(rows, columns, filename)
