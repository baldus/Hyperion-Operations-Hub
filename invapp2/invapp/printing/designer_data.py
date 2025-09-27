"""Metadata used by the in-app label designer UI.

This module doesn't concern itself with rendering printable labels. Instead it
provides structured information that lets the React label designer present the
right set of data bindings for each template. The data defined here mirrors the
contexts used when printing so that users can confidently bind fields that will
resolve at runtime.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable


def _field(
    *,
    id: str,
    label: str,
    field_key: str,
    preview: str,
    description: str,
    field_type: str | None = None,
    default_height: int | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": id,
        "label": label,
        "fieldKey": field_key,
        "preview": preview,
        "description": description,
    }
    if field_type:
        entry["type"] = field_type
    if default_height is not None:
        entry["defaultHeight"] = default_height
    return entry


def _group(
    *, id: str, label: str, description: str, fields: Iterable[dict[str, object]]
) -> dict[str, object]:
    return {
        "id": id,
        "label": label,
        "description": description,
        "fields": list(fields),
    }


_LOT_BATCH_GROUPS = [
    _group(
        id="lot-item",
        label="Item",
        description="Core item master data for the lot or batch.",
        fields=[
            _field(
                id="item-name",
                label="Item Name",
                field_key="inventory.item.name",
                preview="Aluminum Gate Panel",
                description="Primary description of the inventory item.",
            ),
            _field(
                id="item-sku",
                label="Item SKU",
                field_key="inventory.item.sku",
                preview="SKU: GATE-AL-42",
                description="Stock keeping unit or part number for the item.",
            ),
            _field(
                id="item-description",
                label="Item Description",
                field_key="inventory.item.description",
                preview='42" powder coated aluminum gate panel',
                description="Extended item description or notes.",
                default_height=96,
            ),
            _field(
                id="item-type",
                label="Item Type",
                field_key="inventory.item.type",
                preview="Type: Assembly",
                description="Inventory type or classification value.",
            ),
            _field(
                id="item-unit",
                label="Unit of Measure",
                field_key="inventory.item.unit",
                preview="Unit: ea",
                description="Selling or stocking unit of measure.",
            ),
            _field(
                id="item-class",
                label="Item Class",
                field_key="inventory.item.item_class",
                preview="Class: Finished Goods",
                description="Inventory class or reporting bucket.",
            ),
        ],
    ),
    _group(
        id="lot-stock",
        label="Stock",
        description="Quantities and batch tracking values.",
        fields=[
            _field(
                id="stock-quantity",
                label="Quantity",
                field_key="inventory.stock.quantity",
                preview="Qty: 24",
                description="Quantity represented by the label.",
            ),
            _field(
                id="lot-number",
                label="Lot Number",
                field_key="inventory.batch.lot_number",
                preview="Lot #A1-2048",
                description="Supplier or production lot identifier.",
            ),
            _field(
                id="received-date",
                label="Received Date",
                field_key="inventory.batch.received_date",
                preview="Received: 2024-03-12",
                description="Date the batch was received or produced.",
            ),
            _field(
                id="min-stock",
                label="Min Stock",
                field_key="inventory.item.min_stock",
                preview="Min: 12",
                description="Minimum stocking level for the item.",
            ),
            _field(
                id="item-barcode",
                label="Item Barcode",
                field_key="inventory.item.barcode",
                preview="|| ITEM BARCODE ||",
                description="Scannable barcode tied to the SKU or barcode value.",
                field_type="barcode",
                default_height=120,
            ),
        ],
    ),
    _group(
        id="lot-location",
        label="Location",
        description="Storage and handling locations.",
        fields=[
            _field(
                id="location-code",
                label="Location Code",
                field_key="inventory.location.code",
                preview="LOC: RACK-3B",
                description="Warehouse or storage location identifier.",
            ),
            _field(
                id="location-description",
                label="Location Description",
                field_key="inventory.location.description",
                preview="North warehouse - Rack aisle 3, bay B",
                description="Human-friendly description of the storage location.",
                default_height=90,
            ),
        ],
    ),
]


_ORDER_COMPLETION_GROUPS = [
    _group(
        id="order-core",
        label="Order",
        description="Completion summary fields for the order.",
        fields=[
            _field(
                id="order-number",
                label="Order Number",
                field_key="orders.order.number",
                preview="WO-5843",
                description="Work or sales order identifier for the label.",
            ),
            _field(
                id="customer-name",
                label="Customer Name",
                field_key="orders.customer.name",
                preview="Customer: Horizon Builders",
                description="Customer receiving the labeled goods.",
            ),
            _field(
                id="order-barcode",
                label="Order Barcode",
                field_key="orders.order.number",
                preview="|| ORDER BARCODE ||",
                description="Barcode representation of the order number.",
                field_type="barcode",
                default_height=140,
            ),
        ],
    ),
    _group(
        id="order-item",
        label="Item",
        description="Items included on the order.",
        fields=[
            _field(
                id="order-item-number",
                label="Item Number",
                field_key="orders.item.number",
                preview="Item: CTR-2001",
                description="Identifier for the fulfilled item.",
            ),
            _field(
                id="order-item-description",
                label="Item Description",
                field_key="orders.item.description",
                preview="6-stop controller assembly",
                description="Description of the fulfilled item.",
                default_height=96,
            ),
            _field(
                id="order-quantity",
                label="Quantity",
                field_key="orders.item.quantity",
                preview="Qty Completed: 12",
                description="Quantity produced or shipped for the order item.",
            ),
        ],
    ),
    _group(
        id="order-fulfillment",
        label="Fulfillment",
        description="Shipping or due date details when the order ships.",
        fields=[
            _field(
                id="ship-date",
                label="Ship Date",
                field_key="orders.shipment.date",
                preview="Ship: 2024-03-15",
                description="Target shipment or due date for the order.",
            ),
            _field(
                id="ship-carrier",
                label="Carrier",
                field_key="orders.shipment.carrier",
                preview="Carrier: UPS",
                description="Carrier or method used for shipping.",
            ),
        ],
    ),
]


LABEL_DESIGNER_TEMPLATES: list[dict[str, object]] = [
    {
        "id": "lot-batch",
        "name": "Batch / Lot Receiving Label",
        "description": "Used when receiving material into stock.",
        "template_name": "LotBatchLabelTemplate",
        "default_size": {"preset": "4x6", "width": 900, "height": 1350},
        "field_groups": _LOT_BATCH_GROUPS,
        "sample_data": {
            "inventory": {
                "item": {
                    "name": "Aluminum Gate Panel",
                    "sku": "GATE-AL-42",
                    "description": '42" powder coated aluminum gate panel',
                    "type": "Assembly",
                    "unit": "ea",
                    "item_class": "Finished Goods",
                    "min_stock": 12,
                    "barcode": "GATE-AL-42",
                },
                "stock": {"quantity": 24},
                "batch": {
                    "lot_number": "A1-2048",
                    "received_date": "2024-03-12",
                },
                "location": {
                    "code": "RACK-3B",
                    "description": "North warehouse - Rack aisle 3, bay B",
                },
            }
        },
    },
    {
        "id": "order-completion",
        "name": "Order Completion Label",
        "description": "Summarises a completed work or sales order for downstream steps.",
        "template_name": "OrderCompletionLabelTemplate",
        "default_size": {"preset": "4x6", "width": 900, "height": 1350},
        "field_groups": _ORDER_COMPLETION_GROUPS,
        "sample_data": {
            "orders": {
                "order": {"number": "WO-5843"},
                "customer": {"name": "Horizon Builders"},
                "item": {
                    "number": "CTR-2001",
                    "description": "6-stop controller assembly",
                    "quantity": 12,
                },
                "shipment": {"date": "2024-03-15", "carrier": "UPS"},
            }
        },
    },
]


def get_label_designer_templates() -> list[dict[str, object]]:
    """Return a deep copy of the label designer catalog.

    A copy is returned so the caller can freely mutate the returned structures
    (for example to attach persisted layouts) without affecting the module level
    defaults.
    """

    return deepcopy(LABEL_DESIGNER_TEMPLATES)


__all__ = ["get_label_designer_templates"]

