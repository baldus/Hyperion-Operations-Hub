from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from invapp.services.open_orders import compute_open_order_diff, _serialize_snapshot


@dataclass
class FakeLine:
    natural_key: str
    so_no: str
    so_state: str
    so_date: date | None
    ship_by: date | None
    customer_id: str
    customer_name: str
    item_id: str
    line_description: str
    uom: str
    qty_ordered: int
    qty_shipped: int
    qty_remaining: int
    unit_price: Decimal
    part_number: str


def _row(key: str, qty_remaining: int, ship_by_value: date) -> dict:
    return {
        "natural_key": key,
        "so_no": key,
        "so_state": "Open",
        "so_date": date(2024, 9, 1),
        "ship_by": ship_by_value,
        "customer_id": "C-1",
        "customer_name": "Customer",
        "item_id": "ITEM",
        "line_description": "Widget",
        "uom": "EA",
        "qty_ordered": 10,
        "qty_shipped": 0,
        "qty_remaining": qty_remaining,
        "unit_price": Decimal("12.34"),
        "part_number": "PN-1",
    }


def test_compute_open_order_diff_detects_new_completed_changed():
    previous_lines = [
        FakeLine(
            natural_key="A",
            so_no="A",
            so_state="Open",
            so_date=date(2024, 9, 1),
            ship_by=date(2024, 10, 1),
            customer_id="C-1",
            customer_name="Customer",
            item_id="ITEM",
            line_description="Widget",
            uom="EA",
            qty_ordered=10,
            qty_shipped=0,
            qty_remaining=5,
            unit_price=Decimal("12.34"),
            part_number="PN-1",
        ),
        FakeLine(
            natural_key="B",
            so_no="B",
            so_state="Open",
            so_date=date(2024, 9, 1),
            ship_by=date(2024, 10, 1),
            customer_id="C-1",
            customer_name="Customer",
            item_id="ITEM",
            line_description="Widget",
            uom="EA",
            qty_ordered=10,
            qty_shipped=0,
            qty_remaining=5,
            unit_price=Decimal("12.34"),
            part_number="PN-1",
        ),
        FakeLine(
            natural_key="D",
            so_no="D",
            so_state="Open",
            so_date=date(2024, 9, 1),
            ship_by=date(2024, 10, 1),
            customer_id="C-1",
            customer_name="Customer",
            item_id="ITEM",
            line_description="Widget",
            uom="EA",
            qty_ordered=10,
            qty_shipped=0,
            qty_remaining=5,
            unit_price=Decimal("12.34"),
            part_number="PN-1",
        ),
    ]

    current_rows = [
        _row("A", qty_remaining=5, ship_by_value=date(2024, 10, 1)),
        _row("B", qty_remaining=3, ship_by_value=date(2024, 10, 1)),
        _row("C", qty_remaining=2, ship_by_value=date(2024, 11, 1)),
    ]

    diff = compute_open_order_diff(current_rows, previous_lines)

    assert diff.new_keys == {"C"}
    assert diff.completed_keys == {"D"}
    assert diff.still_open_keys == {"A", "B"}
    assert {entry["current"]["natural_key"] for entry in diff.changed_rows} == {"B"}


def test_serialize_snapshot_handles_dates_and_decimals():
    record = {
        "so_date": date(2024, 9, 1),
        "ship_by": date(2024, 10, 1),
        "unit_price": Decimal("12.34"),
        "qty_remaining": 5,
    }

    serialized = _serialize_snapshot(record)

    assert serialized["so_date"] == "2024-09-01"
    assert serialized["ship_by"] == "2024-10-01"
    assert serialized["unit_price"] == "12.34"
    assert serialized["qty_remaining"] == 5
