from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func, or_
from sqlalchemy.orm import load_only

from invapp.auth import blueprint_page_guard
from invapp.models import Item


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
            }
        )

    return jsonify(results)
