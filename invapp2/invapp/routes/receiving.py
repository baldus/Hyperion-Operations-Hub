from __future__ import annotations

from datetime import datetime
from typing import Mapping, Optional, Union

from flask import (
    Blueprint,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy.orm import joinedload

from invapp.extensions import db
from invapp.models import Batch, Item, Location, Movement
from invapp.printing.zebra import print_label_for_process, render_receiving_label_png

bp = Blueprint("receiving", __name__, url_prefix="/receiving")


@bp.route("/")
def receiving_home():
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)
    pagination = (
        Movement.query.options(
            joinedload(Movement.item).load_only(Item.sku, Item.name),
            joinedload(Movement.location).load_only(Location.code),
            joinedload(Movement.batch).load_only(Batch.lot_number),
        )
        .filter_by(movement_type="RECEIPT")
        .order_by(Movement.date.desc())
        .paginate(page=page, per_page=size, error_out=False)
    )
    return render_template(
        "receiving/home.html",
        records=pagination.items,
        page=page,
        size=size,
        pages=pagination.pages,
    )


@bp.route("/add", methods=["GET", "POST"])
def add_receiving():
    locations = Location.query.order_by(Location.code.asc()).all()

    if request.method == "POST":
        sku = request.form["sku"].strip()
        qty = int(request.form["qty"])
        person = request.form["person"].strip()
        po_number = request.form.get("po_number", "").strip() or None
        location_id = int(request.form["location_id"])

        item = Item.query.filter_by(sku=sku).first()
        if not item:
            msg = f"Item with SKU {sku} not found."
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "error": msg}), 400
            flash(msg, "error")
            return redirect(url_for("receiving.add_receiving"))

        lot_number = _generate_lot_number(item)

        batch = Batch(item_id=item.id, lot_number=lot_number, quantity=0)
        if po_number:
            batch.purchase_order = po_number
        db.session.add(batch)
        db.session.flush()

        batch.quantity = (batch.quantity or 0) + qty

        movement = Movement(
            item_id=item.id,
            batch_id=batch.id,
            location_id=location_id,
            quantity=qty,
            movement_type="RECEIPT",
            person=person,
            po_number=po_number,
            reference="PO Receipt" if po_number else "Receipt",
        )
        db.session.add(movement)
        db.session.commit()

        location = next((loc for loc in locations if loc.id == location_id), None)

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            label_url = url_for("receiving.label_preview", receipt_id=movement.id)
            return jsonify(
                {
                    "success": True,
                    "label_url": label_url,
                    "sku": item.sku,
                    "description": item.name,
                    "qty": qty,
                    "lot_number": lot_number,
                    "receipt_id": movement.id,
                    "location": location.code if location else None,
                    "po_number": po_number,
                }
            )

        flash(
            f"Receiving recorded for {item.sku}. Lot {lot_number} created.",
            "success",
        )
        return redirect(url_for("receiving.receiving_home"))

    return render_template("receiving/add.html", locations=locations)


@bp.route("/label-preview")
def label_preview():
    receipt_id = request.args.get("receipt_id", type=int)
    if receipt_id:
        movement = (
            Movement.query.options(
                joinedload(Movement.item),
                joinedload(Movement.location),
                joinedload(Movement.batch),
            )
            .filter_by(id=receipt_id, movement_type="RECEIPT")
            .first_or_404()
        )
        item = movement.item
        batch = movement.batch
        location = movement.location
        image = render_receiving_label_png(
            batch or (item.sku if item else ""),
            item.name if item else movement.reference or "",
            movement.quantity,
            item=item,
            location=location,
            po_number=movement.po_number,
        )
        return Response(image, mimetype="image/png")

    sku = request.args.get("sku", "")
    description = request.args.get("description", sku)
    qty = int(request.args.get("qty", 0))
    image = render_receiving_label_png(sku, description, qty)
    return Response(image, mimetype="image/png")


@bp.route("/print-label", methods=["POST"])
def print_label():
    data = request.get_json() or {}
    receipt_id = data.get("receipt_id")
    if receipt_id is None:
        return jsonify({"printed": False, "error": "receipt_id is required"}), 400

    movement = (
        Movement.query.options(
            joinedload(Movement.item),
            joinedload(Movement.location),
            joinedload(Movement.batch),
        )
        .filter_by(id=receipt_id, movement_type="RECEIPT")
        .first()
    )
    if movement is None:
        return jsonify({"printed": False, "error": "Receipt not found"}), 404

    copies = int(data.get("copies", 1) or 1)
    success = _emit_receipt_labels(movement, copies)
    return jsonify({"printed": success})


@bp.post("/<int:receipt_id>/reprint")
def reprint_receipt(receipt_id: int):
    movement = (
        Movement.query.options(
            joinedload(Movement.item),
            joinedload(Movement.location),
            joinedload(Movement.batch),
        )
        .filter_by(id=receipt_id, movement_type="RECEIPT")
        .first_or_404()
    )

    copies = int(request.form.get("copies", 1) or 1)
    success = _emit_receipt_labels(movement, copies)
    if success:
        flash("Receiving label sent to printer.", "success")
    else:
        flash("Failed to print receiving label.", "warning")

    return redirect(url_for("receiving.receiving_home"))


def _generate_lot_number(item: Item) -> str:
    today_str = datetime.now().strftime("%y%m%d")
    base_lot = f"{item.sku}-{today_str}"
    existing_lots = (
        Batch.query.filter(
            Batch.item_id == item.id,
            Batch.lot_number.like(f"{base_lot}-%"),
        ).count()
    )
    seq_num = existing_lots + 1
    return f"{base_lot}-{seq_num:02d}"


def _emit_receipt_labels(movement: Movement, copies: int = 1) -> bool:
    copies = max(1, copies)
    success = True
    for _ in range(copies):
        success = (
            _print_batch_receipt_label(
                movement.batch,
                movement.item,
                movement.quantity,
                movement.location,
                movement.po_number,
            )
            and success
        )
    return success


def _print_batch_receipt_label(
    batch: Union[Batch, Mapping[str, object], None],
    item: Item,
    qty: int,
    location: Optional[Location],
    po_number: Optional[str],
) -> bool:
    from invapp.printing.labels import build_batch_label_context

    lot_number = (
        getattr(batch, "lot_number", None) if batch is not None else None
    ) or getattr(item, "sku", "")

    batch_source: Union[Batch, Mapping[str, object]]
    if batch is None:
        batch_source = {
            "lot_number": lot_number,
            "quantity": qty,
        }
    else:
        batch_source = batch

    context = build_batch_label_context(
        batch_source,
        item=item,
        quantity=qty,
        location=location,
        po_number=po_number,
    )

    return print_label_for_process("BatchCreated", context)
