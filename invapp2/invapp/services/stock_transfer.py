from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, or_

from invapp.extensions import db
from invapp.models import Batch, Item, Location, Movement
from invapp.services.item_locations import apply_smart_item_locations


@dataclass(frozen=True)
class MoveLineRequest:
    item_id: int
    batch_id: int | None
    quantity: Decimal


def _location_on_hand(item_id: int, batch_id: int | None, location_id: int) -> Decimal:
    filters = [Movement.item_id == item_id, Movement.location_id == location_id]
    if batch_id is None:
        filters.append(Movement.batch_id.is_(None))
    else:
        filters.append(Movement.batch_id == batch_id)

    total = (
        db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
        .filter(*filters)
        .scalar()
    )
    return Decimal(total or 0)


def get_location_inventory_lines(location_id: int) -> list[dict[str, object]]:
    rows = (
        db.session.query(
            Movement.item_id,
            Item.sku,
            Item.name,
            Item.unit,
            Movement.batch_id,
            Batch.lot_number,
            func.coalesce(func.sum(Movement.quantity), 0).label("on_hand"),
        )
        .join(Item, Item.id == Movement.item_id)
        .outerjoin(Batch, Batch.id == Movement.batch_id)
        .filter(Movement.location_id == location_id)
        .filter(or_(Movement.batch_id.is_(None), Batch.removed_at.is_(None)))
        .group_by(
            Movement.item_id,
            Item.sku,
            Item.name,
            Item.unit,
            Movement.batch_id,
            Batch.lot_number,
        )
        .order_by(Item.sku, Batch.lot_number)
        .all()
    )

    lines = []
    for (
        item_id,
        sku,
        name,
        unit,
        batch_id,
        lot_number,
        on_hand,
    ) in rows:
        on_hand_value = float(on_hand or 0)
        presence_status = (
            "present_not_counted" if on_hand_value == 0 else "counted"
        )
        lines.append(
            {
                "item_id": item_id,
                "batch_id": batch_id,
                "sku": sku,
                "name": name,
                "lot_number": lot_number or "",
                "on_hand": on_hand_value,
                "presence_status": presence_status,
                "unit": unit or "",
            }
        )
    return lines


def move_inventory_lines(
    *,
    lines: list[MoveLineRequest],
    from_location_id: int | None,
    to_location_id: int | None,
    person: str | None,
    reference: str,
) -> dict[str, Decimal | int]:
    if not from_location_id or not to_location_id:
        raise ValueError("Select both source and destination locations.")
    if from_location_id == to_location_id:
        raise ValueError("Move locations must be different.")
    if not lines:
        raise ValueError("Select at least one line with a move quantity.")

    from_location = Location.query.get(from_location_id)
    to_location = Location.query.get(to_location_id)
    if from_location is None or to_location is None:
        raise ValueError("Invalid move location selection.")

    item_ids = {line.item_id for line in lines}
    batch_ids = {line.batch_id for line in lines if line.batch_id is not None}
    items = (
        {item.id: item for item in Item.query.filter(Item.id.in_(item_ids)).all()}
        if item_ids
        else {}
    )
    batches = (
        {batch.id: batch for batch in Batch.query.filter(Batch.id.in_(batch_ids)).all()}
        if batch_ids
        else {}
    )

    total_qty = Decimal("0")
    with db.session.begin_nested():
        for line in lines:
            item = items.get(line.item_id)
            if item is None:
                raise ValueError("One or more selected items are invalid.")
            if line.quantity <= 0:
                raise ValueError("Move quantities must be greater than zero.")

            available = _location_on_hand(line.item_id, line.batch_id, from_location_id)
            if line.quantity > available:
                lot_label = "Unbatched"
                if line.batch_id is not None:
                    lot_label = batches.get(line.batch_id, Batch(lot_number="Unknown")).lot_number
                raise ValueError(
                    f"Not enough stock for {item.sku} ({lot_label}). "
                    f"Available {available}."
                )

            db.session.add(
                Movement(
                    item_id=line.item_id,
                    batch_id=line.batch_id,
                    location_id=from_location_id,
                    quantity=-line.quantity,
                    movement_type="MOVE_OUT",
                    person=person,
                    reference=reference,
                )
            )
            db.session.add(
                Movement(
                    item_id=line.item_id,
                    batch_id=line.batch_id,
                    location_id=to_location_id,
                    quantity=line.quantity,
                    movement_type="MOVE_IN",
                    person=person,
                    reference=reference,
                )
            )
            # Apply smart location assignment for the destination location.
            apply_smart_item_locations(item, to_location_id, db.session)
            total_qty += line.quantity

    return {"total_qty": total_qty, "total_lines": len(lines)}
