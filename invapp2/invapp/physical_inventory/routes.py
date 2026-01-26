"""Routes for physical inventory snapshots and reconciliation."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
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
    InventorySnapshotImportIssue,
    InventorySnapshotLine,
    Item,
    Location,
)
from invapp.superuser import superuser_required
from invapp.utils.csv_export import export_rows_to_csv

from .forms import parse_count_updates, parse_snapshot_upload_form
from .services import (
    ImportData,
    MatchPreview,
    NormalizationOptions,
    apply_duplicate_strategy,
    build_count_sheet_rows,
    build_reconciliation_rows,
    build_snapshot_lines,
    build_item_lookup,
    ensure_count_lines_for_snapshot,
    get_item_field_candidates,
    get_item_match_field_options,
    get_item_display_values,
    match_rows,
    parse_import_bytes,
    suggest_column_mappings,
    suggest_matching_upload_column,
    summarize_match_preview,
    summarize_snapshot,
)

bp = Blueprint("physical_inventory", __name__, url_prefix="/physical-inventory")

bp.before_request(blueprint_page_guard("inventory"))

IMPORT_STORAGE_ROOT = os.path.join(tempfile.gettempdir(), "invapp_imports", "physical_inventory")
IMPORT_FILE_TTL_SECONDS = 3600


def _get_import_storage_dir() -> str:
    os.makedirs(IMPORT_STORAGE_ROOT, exist_ok=True)
    return IMPORT_STORAGE_ROOT


def _cleanup_import_storage(now: float | None = None) -> None:
    storage_dir = _get_import_storage_dir()
    current_time = now or time.time()
    try:
        for name in os.listdir(storage_dir):
            path = os.path.join(storage_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                if current_time - os.path.getmtime(path) > IMPORT_FILE_TTL_SECONDS:
                    os.remove(path)
            except OSError:
                continue
    except FileNotFoundError:
        pass


def _store_import_payload(payload: dict) -> str | None:
    _cleanup_import_storage()
    token = secrets.token_urlsafe(16)
    path = os.path.join(_get_import_storage_dir(), f"{token}.json")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError:
        return None
    return token


def _load_import_payload(token: str | None) -> dict | None:
    if not token or any(ch in token for ch in ("/", "\\")):
        return None
    path = os.path.join(_get_import_storage_dir(), f"{token}.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError:
        return None


def _remove_import_payload(token: str | None) -> None:
    if not token or any(ch in token for ch in ("/", "\\")):
        return
    path = os.path.join(_get_import_storage_dir(), f"{token}.json")
    try:
        os.remove(path)
    except OSError:
        pass


def _parse_snapshot_metadata(form: dict[str, str]) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    name = (form.get("name") or "").strip() or None
    source = (form.get("source") or "").strip() or None
    raw_date = (form.get("snapshot_date") or "").strip()
    snapshot_date = None
    if raw_date:
        try:
            snapshot_date = datetime.strptime(raw_date, "%Y-%m-%d")
        except ValueError:
            errors.append("Snapshot date must be in YYYY-MM-DD format.")
    return {
        "name": name,
        "source": source,
        "snapshot_date": snapshot_date,
    }, errors


def _mapping_fields() -> list[dict[str, object]]:
    return [
        {"field": "quantity", "label": "Quantity", "required": True},
        {"field": "description", "label": "Description", "required": False},
        {"field": "uom", "label": "UOM", "required": False},
        {"field": "notes", "label": "Notes", "required": False},
    ]


def _build_preview_table(import_data: ImportData) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in import_data.preview_rows:
        rows.append([row.get(header, "") for header in import_data.headers])
    return rows


def _header_options(import_data: ImportData) -> list[dict[str, str]]:
    return [
        {"original": header, "normalized": normalized}
        for header, normalized in zip(import_data.headers, import_data.normalized_headers)
    ]


def _parse_normalization_options(form: dict[str, str]) -> NormalizationOptions:
    return NormalizationOptions(
        trim=form.get("normalize_trim") == "1",
        case_insensitive=form.get("normalize_case") == "1",
        remove_spaces=form.get("normalize_spaces") == "1",
        remove_dashes=form.get("normalize_dashes") == "1",
    )


def _default_normalization_options() -> NormalizationOptions:
    return NormalizationOptions()


def _default_match_preview() -> MatchPreview:
    return MatchPreview(
        total_rows=0,
        eligible_rows=0,
        matched_rows=0,
        matched_primary=0,
        matched_secondary=0,
        unmatched_rows=0,
        ambiguous_rows=0,
        empty_rows=0,
        match_rate=0.0,
        unmatched_examples=[],
        collision_count=0,
        collision_examples=[],
    )


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
        step = request.form.get("step", "upload")

        if step == "upload":
            payload, errors = parse_snapshot_upload_form(request.form, request.files)
            if errors:
                for message in errors:
                    flash(message, "error")
                return render_template("physical_inventory/snapshot_upload.html")

            file_bytes = payload.file.read()
            import_data, parse_errors = parse_import_bytes(
                payload.file.filename or "",
                file_bytes,
            )
            if parse_errors or import_data is None:
                for message in parse_errors:
                    flash(message, "error")
                return render_template("physical_inventory/snapshot_upload.html")

            import_payload = {
                "headers": import_data.headers,
                "rows": import_data.rows,
                "normalized_headers": import_data.normalized_headers,
            }
            import_token = _store_import_payload(import_payload)
            if not import_token:
                flash("Could not store the uploaded file. Please try again.", "error")
                return render_template("physical_inventory/snapshot_upload.html")

            return render_template(
                "physical_inventory/snapshot_upload.html",
                import_token=import_token,
                headers=import_data.headers,
                normalized_headers=import_data.normalized_headers,
                sample_rows=_build_preview_table(import_data),
            )

        import_token = request.form.get("import_token", "")
        payload = _load_import_payload(import_token)
        if payload is None:
            flash("No import data found. Please upload the file again.", "error")
            return redirect(url_for("physical_inventory.create_snapshot"))

        import_data = ImportData(
            headers=payload.get("headers", []),
            rows=payload.get("rows", []),
            preview_rows=payload.get("rows", [])[:50],
            normalized_headers=payload.get("normalized_headers", []),
        )
        raw_lookup, normalized_lookup, strict_lookup, _, _, desc_fields = build_item_lookup()
        suggestions = suggest_column_mappings(
            import_data,
            raw_lookup,
            normalized_lookup,
            strict_lookup,
            desc_fields,
        )
        header_options = _header_options(import_data)
        item_field_options = get_item_match_field_options()
        string_field_options = [opt for opt in item_field_options if opt.is_string]
        allowed_field_names = {opt.name for opt in item_field_options}
        default_primary_field = next(
            (opt.name for opt in string_field_options if opt.name == "name"),
            string_field_options[0].name if string_field_options else "",
        )
        default_secondary_field = next(
            (opt.name for opt in string_field_options if opt.name == "description"),
            "",
        )
        show_all_fields = request.form.get("show_all_fields") == "1"

        if step == "preview":
            normalization_options = _parse_normalization_options(request.form)
            primary_upload_col = request.form.get("primary_upload_column") or ""
            primary_item_field = request.form.get("primary_item_field") or ""
            use_secondary = request.form.get("use_secondary") == "1"
            secondary_upload_col = request.form.get("secondary_upload_column") or ""
            secondary_item_field = request.form.get("secondary_item_field") or ""
            column_mapping = {
                "quantity": request.form.get("quantity_column") or "",
                "description": request.form.get("description_column") or "",
                "uom": request.form.get("uom_column") or "",
                "notes": request.form.get("notes_column") or "",
            }
            duplicate_strategy = request.form.get("duplicate_strategy", "sum")
            snapshot_meta, meta_errors = _parse_snapshot_metadata(request.form)
            errors: list[str] = []
            for message in meta_errors:
                errors.append(message)

            required_fields = {
                "quantity": "Quantity column",
            }
            if not primary_upload_col:
                errors.append("Primary upload column is required.")
            elif primary_upload_col not in import_data.headers:
                errors.append("Primary upload column is not a valid header.")
            if not primary_item_field:
                errors.append("Primary item field is required.")
            elif primary_item_field not in allowed_field_names:
                errors.append("Primary item field is not a valid option.")
            if use_secondary:
                if not secondary_upload_col:
                    errors.append("Secondary upload column is required when enabled.")
                elif secondary_upload_col not in import_data.headers:
                    errors.append("Secondary upload column is not a valid header.")
                if not secondary_item_field:
                    errors.append("Secondary item field is required when enabled.")
                elif secondary_item_field not in allowed_field_names:
                    errors.append("Secondary item field is not a valid option.")
            for key, label in required_fields.items():
                if not column_mapping[key]:
                    errors.append(f"{label} is required.")
                elif column_mapping[key] not in import_data.headers:
                    errors.append(f"{label} is not a valid header.")

            if errors:
                for message in errors:
                    flash(message, "error")
                return render_template(
                    "physical_inventory/snapshot_mapping.html",
                    import_token=import_token,
                    headers=import_data.headers,
                    header_options=header_options,
                    sample_rows=_build_preview_table(import_data),
                    mapping_fields=_mapping_fields(),
                    selected_mappings=column_mapping,
                    suggestions=suggestions,
                    duplicate_strategy=duplicate_strategy,
                    snapshot_meta=snapshot_meta,
                    item_field_options=item_field_options,
                    string_field_options=string_field_options,
                    primary_upload_column=primary_upload_col,
                    primary_item_field=primary_item_field,
                    secondary_upload_column=secondary_upload_col,
                    secondary_item_field=secondary_item_field,
                    use_secondary=use_secondary,
                    normalization_options=normalization_options,
                    show_all_fields=show_all_fields,
                    match_preview=_default_match_preview(),
                )

            merged_rows, duplicate_groups = apply_duplicate_strategy(
                import_data.rows,
                primary_upload_col,
                secondary_upload_col if use_secondary else None,
                column_mapping["quantity"],
                duplicate_strategy,
                normalization_options,
            )
            items = Item.query.all()
            matches, collision_count, collision_examples = match_rows(
                merged_rows,
                primary_upload_col,
                primary_item_field,
                secondary_upload_col if use_secondary else None,
                secondary_item_field if use_secondary else None,
                normalization_options,
                items,
            )
            match_preview = summarize_match_preview(
                merged_rows,
                matches,
                use_secondary,
                collision_count,
                collision_examples,
            )
            current_app.logger.info(
                "Snapshot matching preview: primary_field=%s secondary_field=%s normalize=%s",
                primary_item_field,
                secondary_item_field if use_secondary else None,
                normalization_options,
            )
            return render_template(
                "physical_inventory/snapshot_mapping.html",
                import_token=import_token,
                headers=import_data.headers,
                header_options=header_options,
                sample_rows=_build_preview_table(import_data),
                mapping_fields=_mapping_fields(),
                selected_mappings=column_mapping,
                suggestions=suggestions,
                duplicate_strategy=duplicate_strategy,
                snapshot_meta=snapshot_meta,
                item_field_options=item_field_options,
                string_field_options=string_field_options,
                primary_upload_column=primary_upload_col,
                primary_item_field=primary_item_field,
                secondary_upload_column=secondary_upload_col,
                secondary_item_field=secondary_item_field,
                use_secondary=use_secondary,
                normalization_options=normalization_options,
                show_all_fields=show_all_fields,
                match_preview=match_preview,
                duplicate_groups=duplicate_groups,
            )

        if step == "commit":
            normalization_options = _parse_normalization_options(request.form)
            primary_upload_col = request.form.get("primary_upload_column") or ""
            primary_item_field = request.form.get("primary_item_field") or ""
            use_secondary = request.form.get("use_secondary") == "1"
            secondary_upload_col = request.form.get("secondary_upload_column") or ""
            secondary_item_field = request.form.get("secondary_item_field") or ""
            column_mapping = {
                "quantity": request.form.get("quantity_column") or "",
                "description": request.form.get("description_column") or "",
                "uom": request.form.get("uom_column") or "",
                "notes": request.form.get("notes_column") or "",
            }
            duplicate_strategy = request.form.get("duplicate_strategy", "sum")
            snapshot_meta, meta_errors = _parse_snapshot_metadata(request.form)
            if meta_errors:
                for message in meta_errors:
                    flash(message, "error")
                return redirect(
                    url_for("physical_inventory.create_snapshot", step="map", import_token=import_token)
                )
            if not primary_upload_col or not primary_item_field:
                flash("Primary matching configuration is required.", "error")
                return redirect(
                    url_for("physical_inventory.create_snapshot", step="map", import_token=import_token)
                )
            if primary_item_field not in allowed_field_names:
                flash("Primary item field is not a valid option.", "error")
                return redirect(
                    url_for("physical_inventory.create_snapshot", step="map", import_token=import_token)
                )
            if use_secondary and (not secondary_upload_col or not secondary_item_field):
                flash("Secondary matching configuration is incomplete.", "error")
                return redirect(
                    url_for("physical_inventory.create_snapshot", step="map", import_token=import_token)
                )
            if use_secondary and secondary_item_field not in allowed_field_names:
                flash("Secondary item field is not a valid option.", "error")
                return redirect(
                    url_for("physical_inventory.create_snapshot", step="map", import_token=import_token)
                )
            if not column_mapping["quantity"]:
                flash("Quantity mapping is required.", "error")
                return redirect(
                    url_for("physical_inventory.create_snapshot", step="map", import_token=import_token)
                )

            merged_rows, _ = apply_duplicate_strategy(
                import_data.rows,
                primary_upload_col,
                secondary_upload_col if use_secondary else None,
                column_mapping["quantity"],
                duplicate_strategy,
                normalization_options,
            )
            items = Item.query.all()
            matches, _, _ = match_rows(
                merged_rows,
                primary_upload_col,
                primary_item_field,
                secondary_upload_col if use_secondary else None,
                secondary_item_field if use_secondary else None,
                normalization_options,
                items,
            )
            snapshot_lines, line_errors = build_snapshot_lines(
                merged_rows,
                matches,
                primary_upload_col,
                secondary_upload_col if use_secondary else None,
                column_mapping["description"] or None,
                column_mapping["quantity"],
                column_mapping["uom"] or None,
                column_mapping["notes"] or None,
            )
            if line_errors:
                for message in line_errors:
                    flash(message, "error")
                return redirect(
                    url_for("physical_inventory.create_snapshot", step="map", import_token=import_token)
                )

            snapshot = InventorySnapshot(
                name=snapshot_meta["name"],
                snapshot_date=snapshot_meta["snapshot_date"] or datetime.utcnow(),
                source=snapshot_meta["source"] or "Import",
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
                    source_part_number_text=row.primary_match_text,
                    source_secondary_match_text=row.secondary_match_text,
                    source_description_text=row.source_description_text,
                )
                for row in snapshot_lines
            ]
            db.session.add_all(lines)
            issues = [
                InventorySnapshotImportIssue(
                    snapshot_id=snapshot.id,
                    row_index=index,
                    reason=match.reason,
                    primary_value=match.primary_value,
                    secondary_value=match.secondary_value,
                    row_data=row,
                )
                for index, (row, match) in enumerate(zip(merged_rows, matches), start=1)
                if match.item_id is None
            ]
            db.session.add_all(issues)
            created_count_lines = ensure_count_lines_for_snapshot(snapshot)
            db.session.commit()
            _remove_import_payload(import_token)

            flash(
                f"Snapshot created with {len(lines)} item totals and {created_count_lines} count lines.",
                "success",
            )
            current_app.logger.info(
                "Snapshot matching commit: primary_field=%s secondary_field=%s normalize=%s",
                primary_item_field,
                secondary_item_field if use_secondary else None,
                normalization_options,
            )
            return redirect(
                url_for("physical_inventory.import_results", snapshot_id=snapshot.id)
            )

    step = request.args.get("step")
    import_token = request.args.get("import_token")
    if step == "map" and import_token:
        payload = _load_import_payload(import_token)
        if payload is None:
            flash("No import data found. Please upload the file again.", "error")
            return redirect(url_for("physical_inventory.create_snapshot"))
        import_data = ImportData(
            headers=payload.get("headers", []),
            rows=payload.get("rows", []),
            preview_rows=payload.get("rows", [])[:50],
            normalized_headers=payload.get("normalized_headers", []),
        )
        raw_lookup, normalized_lookup, strict_lookup, _, _, desc_fields = build_item_lookup()
        suggestions = suggest_column_mappings(
            import_data,
            raw_lookup,
            normalized_lookup,
            strict_lookup,
            desc_fields,
        )
        header_options = _header_options(import_data)
        item_field_options = get_item_match_field_options()
        string_field_options = [opt for opt in item_field_options if opt.is_string]
        default_primary_field = next(
            (opt.name for opt in string_field_options if opt.name == "name"),
            string_field_options[0].name if string_field_options else "",
        )
        default_secondary_field = next(
            (opt.name for opt in string_field_options if opt.name == "description"),
            "",
        )
        items = Item.query.all()
        normalization_options = _default_normalization_options()
        suggested_primary_column = (
            suggest_matching_upload_column(
                import_data,
                items,
                default_primary_field,
                normalization_options,
            )
            if default_primary_field
            else None
        )
        return render_template(
            "physical_inventory/snapshot_mapping.html",
            import_token=import_token,
            headers=import_data.headers,
            header_options=header_options,
            sample_rows=_build_preview_table(import_data),
            mapping_fields=_mapping_fields(),
            selected_mappings={
                "quantity": suggestions.get("quantity") or "",
                "description": suggestions.get("description") or "",
                "uom": suggestions.get("uom") or "",
                "notes": suggestions.get("notes") or "",
            },
            suggestions=suggestions,
            duplicate_strategy="sum",
            snapshot_meta={"name": None, "source": None, "snapshot_date": None},
            item_field_options=item_field_options,
            string_field_options=string_field_options,
            primary_upload_column=suggested_primary_column or "",
            primary_item_field=default_primary_field,
            secondary_upload_column="",
            secondary_item_field=default_secondary_field,
            use_secondary=False,
            normalization_options=normalization_options,
            show_all_fields=False,
            match_preview=_default_match_preview(),
            duplicate_groups=0,
        )

    return render_template("physical_inventory/snapshot_upload.html")


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


@bp.route("/snapshots/<int:snapshot_id>/import-results")
@superuser_required
def import_results(snapshot_id: int):
    snapshot = InventorySnapshot.query.get_or_404(snapshot_id)
    summary = summarize_snapshot(snapshot.id)
    issue_count = (
        db.session.query(InventorySnapshotImportIssue)
        .filter_by(snapshot_id=snapshot.id)
        .count()
    )
    return render_template(
        "physical_inventory/snapshot_import_results.html",
        snapshot=snapshot,
        summary=summary,
        issue_count=issue_count,
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
        part_fields, desc_fields = get_item_field_candidates()

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
                    part_number, _ = get_item_display_values(
                        line.item, part_fields, desc_fields
                    )
                    errors.append(
                        f"Line {line.id} ({part_number}): counted_qty must be numeric."
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
        .order_by(Item.id)
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
    part_fields, desc_fields = get_item_field_candidates()
    item_display = {
        line.item_id: get_item_display_values(line.item, part_fields, desc_fields)
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
        item_display=item_display,
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


@bp.route("/snapshots/<int:snapshot_id>/export/import-issues.csv")
@superuser_required
def export_import_issues(snapshot_id: int):
    snapshot = InventorySnapshot.query.get_or_404(snapshot_id)
    issues = (
        InventorySnapshotImportIssue.query.filter_by(snapshot_id=snapshot.id)
        .order_by(InventorySnapshotImportIssue.row_index)
        .all()
    )
    if not issues:
        flash("No import issues were recorded for this snapshot.", "warning")
        return redirect(url_for("physical_inventory.import_results", snapshot_id=snapshot.id))

    row_headers: list[str] = []
    for issue in issues:
        row_data = issue.row_data or {}
        for key in row_data.keys():
            if key not in row_headers:
                row_headers.append(key)

    rows: list[dict[str, object]] = []
    for issue in issues:
        row = {
            "row_index": issue.row_index,
            "reason": issue.reason,
            "primary_value": issue.primary_value,
            "secondary_value": issue.secondary_value,
        }
        row_data = issue.row_data or {}
        for header in row_headers:
            row[header] = row_data.get(header, "")
        rows.append(row)

    columns = [
        ("row_index", "row_index"),
        ("reason", "reason"),
        ("primary_value", "primary_value"),
        ("secondary_value", "secondary_value"),
        *[(header, header) for header in row_headers],
    ]
    filename = f"physical_inventory_import_issues_{snapshot.id}.csv"
    return export_rows_to_csv(rows, columns, filename)
