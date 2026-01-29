from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func, or_
from sqlalchemy.orm import load_only

from invapp.auth import blueprint_page_guard
from invapp.models import Item, Location, Movement, db


bp = Blueprint("item_search", __name__, url_prefix="/api")

bp.before_request(blueprint_page_guard("purchasing"))


@bp.get("/items/search")
def search_items():
    """Search items for quick purchasing lookup.

    To extend search criteria, add fields to ``match_filter`` and update
    ``ranking`` so new columns are ordered deterministically.
    """

    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"error": "Query must be at least 2 characters."}), 400
    if len(query) > 80:
        return jsonify({"error": "Query must be 80 characters or fewer."}), 400

    lowered = query.lower()
    contains_pattern = f"%{lowered}%"
    prefix_pattern = f"{lowered}%"

    match_filter = or_(
        func.lower(Item.sku).like(contains_pattern),
        func.lower(Item.name).like(contains_pattern),
        func.lower(Item.description).like(contains_pattern),
    )

    ranking = case(
        (func.lower(Item.sku) == lowered, 0),
        (func.lower(Item.sku).like(prefix_pattern), 1),
        (func.lower(Item.sku).like(contains_pattern), 2),
        else_=3,
    )

    matches = (
        Item.query.options(
            load_only(
                Item.id,
                Item.sku,
                Item.name,
                Item.description,
                Item.unit,
                Item.min_stock,
                Item.item_class,
                Item.type,
            )
        )
        .filter(match_filter)
        .order_by(ranking, Item.sku)
        .limit(10)
        .all()
    )

    item_ids = [item.id for item in matches]
    totals_map: dict[int, float] = {}
    locations_map: dict[int, list[dict[str, str | float]]] = {}
    if item_ids:
        totals = (
            db.session.query(
                Movement.item_id,
                func.coalesce(func.sum(Movement.quantity), 0),
            )
            .filter(Movement.item_id.in_(item_ids))
            .group_by(Movement.item_id)
            .all()
        )
        totals_map = {item_id: float(total or 0) for item_id, total in totals}

        location_rows = (
            db.session.query(
                Movement.item_id,
                Location.code,
                Location.description,
                func.coalesce(func.sum(Movement.quantity), 0),
            )
            .join(Location, Location.id == Movement.location_id)
            .filter(Movement.item_id.in_(item_ids))
            .filter(Location.removed_at.is_(None))
            .group_by(Movement.item_id, Location.code, Location.description)
            .order_by(Location.code)
            .all()
        )

        for item_id, code, description, total in location_rows:
            locations_map.setdefault(item_id, []).append(
                {
                    "code": code,
                    "description": description or "",
                    "quantity": float(total or 0),
                }
            )

    results = []
    for item in matches:
        results.append(
            {
                "id": item.id,
                "item_number": item.sku,
                "name": item.name,
                "description": item.description or item.name,
                "uom": item.unit or "",
                "default_reorder_qty": item.min_stock,
                "preferred_supplier_id": None,
                "preferred_supplier_name": None,
                "category": item.item_class or item.type,
                "on_hand_total": totals_map.get(item.id, 0),
                "locations": locations_map.get(item.id, []),
            }
        )

    return jsonify(results)


@bp.get("/items/<int:item_id>/stock")
def item_stock(item_id: int):
    """Return on-hand totals for a single item for live purchasing updates."""

    item = db.session.get(Item, item_id)
    if item is None:
        return jsonify({"error": "Item not found."}), 404

    total = (
        db.session.query(func.coalesce(func.sum(Movement.quantity), 0))
        .filter(Movement.item_id == item_id)
        .scalar()
    )
    total_value = float(total or 0)

    location_rows = (
        db.session.query(
            Location.code,
            Location.description,
            func.coalesce(func.sum(Movement.quantity), 0),
        )
        .join(Location, Location.id == Movement.location_id)
        .filter(Movement.item_id == item_id)
        .filter(Location.removed_at.is_(None))
        .group_by(Location.code, Location.description)
        .order_by(Location.code)
        .all()
    )

    locations = [
        {
            "code": code,
            "description": description or "",
            "quantity": float(quantity or 0),
        }
        for code, description, quantity in location_rows
    ]

    return jsonify({"item_id": item_id, "on_hand_total": total_value, "locations": locations})
