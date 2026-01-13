from __future__ import annotations

from sqlalchemy import func

from invapp.models import Item, Movement


def _primary_has_stock(item: Item, session) -> bool:
    if item.default_location_id is None:
        return False

    total = (
        session.query(func.coalesce(func.sum(Movement.quantity), 0))
        .filter(
            Movement.item_id == item.id,
            Movement.location_id == item.default_location_id,
        )
        .scalar()
    )
    return (total or 0) > 0


def apply_smart_item_locations(item: Item, selected_location_id: int | None, session) -> None:
    if selected_location_id is None:
        return

    if item.default_location_id is None:
        item.default_location_id = selected_location_id
        return

    if selected_location_id == item.default_location_id:
        return

    if item.secondary_location_id is not None:
        return

    if _primary_has_stock(item, session):
        item.secondary_location_id = selected_location_id
