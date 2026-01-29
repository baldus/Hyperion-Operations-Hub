"""Label template registry and ZPL rendering utilities."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

from flask import current_app, has_app_context

LABEL_WIDTH = 812  # dots for 4" width at 203 DPI
LABEL_HEIGHT = 1218  # dots for 6" height at 203 DPI

_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([^}]+)\s*\}\}")

if TYPE_CHECKING:  # pragma: no cover - hints only
    from invapp.models import Batch, Item, Location


@dataclass(frozen=True)
class LabelDefinition:
    """In-memory representation of a printable label template."""

    name: str
    layout: Mapping[str, Any]
    fields: Mapping[str, str]
    description: str | None = None
    triggers: tuple[str, ...] = ()

    def render(self, context: Mapping[str, Any]) -> str:
        values = _resolve_fields(self.fields, context)
        return _render_layout(self.layout, values)


@dataclass(frozen=True)
class DesignerFieldBinding:
    key: str
    label: str
    expression: str


@dataclass(frozen=True)
class DesignerLabelConfig:
    id: str
    name: str
    description: str
    template_name: str
    process: str
    data_fields: tuple[DesignerFieldBinding, ...]
    sample_data: Mapping[str, str]
    sample_context: Mapping[str, Any]
    default_layout: Mapping[str, Any]


LABEL_DEFINITIONS: dict[str, LabelDefinition] = {}
PROCESS_ASSIGNMENTS: dict[str, str] = {}

ALIGNMENT_TO_JUSTIFY = {"left": "L", "center": "C", "right": "R"}
JUSTIFY_TO_ALIGNMENT = {value: key for key, value in ALIGNMENT_TO_JUSTIFY.items()}
ORIENTATION_TO_DEGREES = {"N": 0, "R": 90, "I": 180, "B": 270}


def _build_data_field_list(bindings: tuple[DesignerFieldBinding, ...]) -> list[dict[str, str]]:
    return [{"key": binding.key, "label": binding.label} for binding in bindings]


BATCH_SAMPLE_DATA = {
    "lot_number": "LOT-00977-A",
    "product_name": "Widget Prime - Stainless",
    "product_description": "Stainless steel widget with reinforced housing",
    "sku": "SKU-12345",
    "quantity": "120",
    "unit": "ea",
    "expiration_date": "2025-03-31",
    "received_date": "2024-05-21",
    "supplier_name": "Atlas Components",
    "supplier_code": "ATLAS-001",
    "po_number": "PO-44210",
    "location": "RCV-01",
    "notes": "Keep refrigerated",
}


BATCH_FIELD_BINDINGS: tuple[DesignerFieldBinding, ...] = (
    DesignerFieldBinding("lot_number", "Lot Number", "{{Batch.LotNumber}}"),
    DesignerFieldBinding("product_name", "Product Name", "{{Item.Name}}"),
    DesignerFieldBinding("product_description", "Product Description", "{{Item.Description}}"),
    DesignerFieldBinding("sku", "SKU", "{{Item.SKU}}"),
    DesignerFieldBinding("quantity", "Quantity", "{{Batch.Quantity}}"),
    DesignerFieldBinding("unit", "Unit", "{{Batch.Unit}}"),
    DesignerFieldBinding("expiration_date", "Expiration Date", "{{Batch.ExpirationDate}}"),
    DesignerFieldBinding("received_date", "Received Date", "{{Batch.ReceivedDate}}"),
    DesignerFieldBinding("supplier_name", "Supplier Name", "{{Batch.SupplierName}}"),
    DesignerFieldBinding("supplier_code", "Supplier Code", "{{Batch.SupplierCode}}"),
    DesignerFieldBinding("po_number", "Purchase Order", "{{Batch.PurchaseOrder}}"),
    DesignerFieldBinding("location", "Storage Location", "{{Location.Code}}"),
    DesignerFieldBinding("notes", "Notes", "{{Batch.Notes}}"),
)


BATCH_SAMPLE_CONTEXT = {
    "Batch": {
        "LotNumber": BATCH_SAMPLE_DATA["lot_number"],
        "Quantity": BATCH_SAMPLE_DATA["quantity"],
        "Unit": BATCH_SAMPLE_DATA["unit"],
        "ExpirationDate": BATCH_SAMPLE_DATA["expiration_date"],
        "ReceivedDate": BATCH_SAMPLE_DATA["received_date"],
        "SupplierName": BATCH_SAMPLE_DATA["supplier_name"],
        "SupplierCode": BATCH_SAMPLE_DATA["supplier_code"],
        "PurchaseOrder": BATCH_SAMPLE_DATA["po_number"],
        "Notes": BATCH_SAMPLE_DATA["notes"],
    },
    "Item": {
        "Name": BATCH_SAMPLE_DATA["product_name"],
        "SKU": BATCH_SAMPLE_DATA["sku"],
        "Description": BATCH_SAMPLE_DATA["product_description"],
    },
    "Location": {"Code": BATCH_SAMPLE_DATA["location"]},
}


BATCH_DEFAULT_FIELDS = [
    {
        "id": "field-title",
        "label": "Batch Label",
        "bindingKey": None,
        "type": "text",
        "x": 60,
        "y": 40,
        "width": 692,
        "height": 72,
        "rotation": 0,
        "fontSize": 64,
        "align": "center",
    },
    {
        "id": "field-lot-number",
        "label": "Lot Number",
        "bindingKey": "lot_number",
        "type": "text",
        "x": 60,
        "y": 140,
        "width": 692,
        "height": 60,
        "rotation": 0,
        "fontSize": 52,
        "align": "left",
    },
    {
        "id": "field-product-name",
        "label": "Product Name",
        "bindingKey": "product_name",
        "type": "text",
        "x": 60,
        "y": 220,
        "width": 692,
        "height": 52,
        "rotation": 0,
        "fontSize": 40,
        "align": "left",
    },
    {
        "id": "field-sku",
        "label": "SKU",
        "bindingKey": "sku",
        "type": "text",
        "x": 60,
        "y": 290,
        "width": 320,
        "height": 44,
        "rotation": 0,
        "fontSize": 34,
        "align": "left",
    },
    {
        "id": "field-quantity",
        "label": "Qty",
        "bindingKey": "quantity",
        "type": "text",
        "x": 400,
        "y": 290,
        "width": 160,
        "height": 44,
        "rotation": 0,
        "fontSize": 34,
        "align": "left",
    },
    {
        "id": "field-unit",
        "label": "Unit",
        "bindingKey": "unit",
        "type": "text",
        "x": 580,
        "y": 290,
        "width": 172,
        "height": 44,
        "rotation": 0,
        "fontSize": 34,
        "align": "left",
    },
    {
        "id": "field-supplier",
        "label": "Supplier",
        "bindingKey": "supplier_name",
        "type": "text",
        "x": 60,
        "y": 360,
        "width": 512,
        "height": 44,
        "rotation": 0,
        "fontSize": 32,
        "align": "left",
    },
    {
        "id": "field-po",
        "label": "PO Number",
        "bindingKey": "po_number",
        "type": "text",
        "x": 60,
        "y": 420,
        "width": 320,
        "height": 40,
        "rotation": 0,
        "fontSize": 30,
        "align": "left",
    },
    {
        "id": "field-expiration",
        "label": "Expires",
        "bindingKey": "expiration_date",
        "type": "text",
        "x": 400,
        "y": 420,
        "width": 352,
        "height": 40,
        "rotation": 0,
        "fontSize": 30,
        "align": "left",
    },
    {
        "id": "field-received",
        "label": "Received",
        "bindingKey": "received_date",
        "type": "text",
        "x": 60,
        "y": 480,
        "width": 320,
        "height": 40,
        "rotation": 0,
        "fontSize": 28,
        "align": "left",
    },
    {
        "id": "field-location",
        "label": "Location",
        "bindingKey": "location",
        "type": "text",
        "x": 400,
        "y": 480,
        "width": 352,
        "height": 40,
        "rotation": 0,
        "fontSize": 28,
        "align": "left",
    },
    {
        "id": "field-notes",
        "label": "Notes",
        "bindingKey": "notes",
        "type": "text",
        "x": 60,
        "y": 540,
        "width": 692,
        "height": 60,
        "rotation": 0,
        "fontSize": 26,
        "align": "left",
    },
    {
        "id": "field-lot-barcode",
        "label": "Lot Barcode",
        "bindingKey": "lot_number",
        "type": "barcode",
        "x": 60,
        "y": 640,
        "width": 692,
        "height": 220,
        "rotation": 0,
        "fontSize": 20,
        "align": "center",
        "showValue": True,
    },
]


BATCH_DEFAULT_LAYOUT = {
    "id": "batch-label",
    "name": "Batch Label",
    "description": "Detailed batch label with full traceability data and scan-ready barcode.",
    "size": {"width": LABEL_WIDTH, "height": LABEL_HEIGHT},
    "dataFields": _build_data_field_list(BATCH_FIELD_BINDINGS),
    "sampleData": dict(BATCH_SAMPLE_DATA),
    "fields": deepcopy(BATCH_DEFAULT_FIELDS),
}


ORDER_SAMPLE_ITEM_ID = "GATE-AL-42"


ORDER_SAMPLE_DATA = {
    "order_number": "Order #PO-8821",
    "customer_name": "Customer: Hyperion Labs",
    "address_line": "Address: 991 Market Street",
    "item_id": f"Item: {ORDER_SAMPLE_ITEM_ID}",
    "city_state": "San Francisco, CA",
    "due_date": "Due 06/01",
}


ORDER_FIELD_BINDINGS: tuple[DesignerFieldBinding, ...] = (
    DesignerFieldBinding("order_number", "Order Number", "Order #{{Order.ID}}"),
    DesignerFieldBinding("customer_name", "Customer Name", "Customer: {{Order.CustomerName}}"),
    DesignerFieldBinding("address_line", "Address", "Address: {{Order.Address}}"),
    DesignerFieldBinding("item_id", "Item", "Item: {{Order.ItemID}}"),
    DesignerFieldBinding("city_state", "City / State", "{{Order.CityState}}"),
    DesignerFieldBinding("due_date", "Due Date", "{{Order.DueDate}}"),
)


ORDER_SAMPLE_CONTEXT = {
    "Order": {
        "ID": ORDER_SAMPLE_DATA["order_number"],
        "CustomerName": ORDER_SAMPLE_DATA["customer_name"],
        "Address": ORDER_SAMPLE_DATA["address_line"],
        "ItemID": ORDER_SAMPLE_ITEM_ID,
        "CityState": ORDER_SAMPLE_DATA["city_state"],
        "DueDate": ORDER_SAMPLE_DATA["due_date"],
    }
}


ORDER_DEFAULT_FIELDS = [
    {
        "id": "field-order-number",
        "label": "Order Number",
        "bindingKey": "order_number",
        "type": "text",
        "x": 40,
        "y": 40,
        "width": 732,
        "height": 60,
        "rotation": 0,
        "fontSize": 52,
        "align": "left",
    },
    {
        "id": "field-customer",
        "label": "Customer",
        "bindingKey": "customer_name",
        "type": "text",
        "x": 40,
        "y": 110,
        "width": 732,
        "height": 48,
        "rotation": 0,
        "fontSize": 36,
        "align": "left",
    },
    {
        "id": "field-address",
        "label": "Address",
        "bindingKey": "address_line",
        "type": "text",
        "x": 40,
        "y": 180,
        "width": 732,
        "height": 48,
        "rotation": 0,
        "fontSize": 30,
        "align": "left",
    },
    {
        "id": "field-item",
        "label": "Item",
        "bindingKey": "item_id",
        "type": "text",
        "x": 40,
        "y": 240,
        "width": 732,
        "height": 44,
        "rotation": 0,
        "fontSize": 28,
        "align": "left",
    },
    {
        "id": "field-city-state",
        "label": "City / State",
        "bindingKey": "city_state",
        "type": "text",
        "x": 40,
        "y": 300,
        "width": 732,
        "height": 44,
        "rotation": 0,
        "fontSize": 28,
        "align": "left",
    },
    {
        "id": "field-due-date",
        "label": "Due Date",
        "bindingKey": "due_date",
        "type": "text",
        "x": 40,
        "y": 360,
        "width": 360,
        "height": 44,
        "rotation": 0,
        "fontSize": 28,
        "align": "left",
    },
    {
        "id": "field-order-barcode",
        "label": "Order Barcode",
        "bindingKey": "order_number",
        "type": "barcode",
        "x": 40,
        "y": 420,
        "width": 732,
        "height": 200,
        "rotation": 0,
        "fontSize": 18,
        "align": "center",
        "showValue": True,
    },
]


ORDER_DEFAULT_LAYOUT = {
    "id": "order-label",
    "name": "Order Completion Label",
    "description": "Order completion summary label.",
    "size": {"width": LABEL_WIDTH, "height": 700},
    "dataFields": _build_data_field_list(ORDER_FIELD_BINDINGS),
    "sampleData": dict(ORDER_SAMPLE_DATA),
    "fields": deepcopy(ORDER_DEFAULT_FIELDS),
}


LOCATION_SAMPLE_DATA = {
    "code": "RACK-A1-01",
    "description": "Aisle A / Rack 1 / Shelf 01",
}


LOCATION_FIELD_BINDINGS: tuple[DesignerFieldBinding, ...] = (
    DesignerFieldBinding("code", "Location Code", "{{Location.Code}}"),
    DesignerFieldBinding("description", "Description", "{{Location.Description}}"),
)


LOCATION_SAMPLE_CONTEXT = {
    "Location": {
        "Code": LOCATION_SAMPLE_DATA["code"],
        "Description": LOCATION_SAMPLE_DATA["description"],
    }
}


LOCATION_DEFAULT_FIELDS = [
    {
        "id": "field-location-title",
        "label": "Inventory Location",
        "bindingKey": None,
        "type": "text",
        "x": 60,
        "y": 40,
        "width": 692,
        "height": 64,
        "rotation": 0,
        "fontSize": 48,
        "align": "center",
    },
    {
        "id": "field-location-code",
        "label": "Location Code",
        "bindingKey": "code",
        "type": "text",
        "x": 60,
        "y": 140,
        "width": 692,
        "height": 80,
        "rotation": 0,
        "fontSize": 64,
        "align": "center",
    },
    {
        "id": "field-location-description",
        "label": "Description",
        "bindingKey": "description",
        "type": "text",
        "x": 60,
        "y": 240,
        "width": 692,
        "height": 60,
        "rotation": 0,
        "fontSize": 30,
        "align": "center",
    },
    {
        "id": "field-location-barcode",
        "label": "Location Barcode",
        "bindingKey": "code",
        "type": "barcode",
        "x": 80,
        "y": 340,
        "width": 652,
        "height": 200,
        "rotation": 0,
        "fontSize": 18,
        "align": "center",
        "showValue": True,
    },
]


LOCATION_DEFAULT_LAYOUT = {
    "id": "location-label",
    "name": "Location Label",
    "description": "Barcode label for fixed storage locations.",
    "size": {"width": LABEL_WIDTH, "height": 600},
    "dataFields": _build_data_field_list(LOCATION_FIELD_BINDINGS),
    "sampleData": dict(LOCATION_SAMPLE_DATA),
    "fields": deepcopy(LOCATION_DEFAULT_FIELDS),
}

ITEM_SAMPLE_DATA = {
    "sku": "ITEM-204",
    "name": "Valve Assembly",
    "description": "Stainless steel valve assembly",
    "unit": "ea",
}


ITEM_FIELD_BINDINGS: tuple[DesignerFieldBinding, ...] = (
    DesignerFieldBinding("sku", "SKU", "{{Item.SKU}}"),
    DesignerFieldBinding("name", "Item Name", "{{Item.Name}}"),
    DesignerFieldBinding("description", "Description", "{{Item.Description}}"),
    DesignerFieldBinding("unit", "Unit", "{{Item.Unit}}"),
)


ITEM_SAMPLE_CONTEXT = {
    "Item": {
        "SKU": ITEM_SAMPLE_DATA["sku"],
        "Name": ITEM_SAMPLE_DATA["name"],
        "Description": ITEM_SAMPLE_DATA["description"],
        "Unit": ITEM_SAMPLE_DATA["unit"],
    }
}


ITEM_DEFAULT_FIELDS = [
    {
        "id": "field-item-title",
        "label": "Item Label",
        "bindingKey": None,
        "type": "text",
        "x": 60,
        "y": 40,
        "width": 692,
        "height": 64,
        "rotation": 0,
        "fontSize": 48,
        "align": "center",
    },
    {
        "id": "field-item-sku",
        "label": "SKU",
        "bindingKey": "sku",
        "type": "text",
        "x": 60,
        "y": 140,
        "width": 692,
        "height": 60,
        "rotation": 0,
        "fontSize": 52,
        "align": "left",
    },
    {
        "id": "field-item-name",
        "label": "Item Name",
        "bindingKey": "name",
        "type": "text",
        "x": 60,
        "y": 220,
        "width": 692,
        "height": 50,
        "rotation": 0,
        "fontSize": 36,
        "align": "left",
    },
    {
        "id": "field-item-description",
        "label": "Description",
        "bindingKey": "description",
        "type": "text",
        "x": 60,
        "y": 290,
        "width": 692,
        "height": 48,
        "rotation": 0,
        "fontSize": 30,
        "align": "left",
    },
    {
        "id": "field-item-unit",
        "label": "Unit",
        "bindingKey": "unit",
        "type": "text",
        "x": 60,
        "y": 360,
        "width": 300,
        "height": 44,
        "rotation": 0,
        "fontSize": 28,
        "align": "left",
    },
    {
        "id": "field-item-barcode",
        "label": "Item Barcode",
        "bindingKey": "sku",
        "type": "barcode",
        "x": 80,
        "y": 430,
        "width": 652,
        "height": 200,
        "rotation": 0,
        "fontSize": 18,
        "align": "center",
        "showValue": True,
    },
]


ITEM_DEFAULT_LAYOUT = {
    "id": "item-label",
    "name": "Item Label",
    "description": "Standard inventory item identification label.",
    "size": {"width": LABEL_WIDTH, "height": 700},
    "dataFields": _build_data_field_list(ITEM_FIELD_BINDINGS),
    "sampleData": dict(ITEM_SAMPLE_DATA),
    "fields": deepcopy(ITEM_DEFAULT_FIELDS),
}


TRANSFER_SAMPLE_DATA = {
    "sku": "ITEM-204",
    "name": "Valve Assembly",
    "lot_number": "LOT-882",
    "quantity": "12",
    "unit": "ea",
    "from_location": "STG-01",
    "to_location": "LINE-04",
    "reference": "Stock Transfer",
    "person": "j.smith",
    "timestamp": "2024-05-21 14:22",
}


TRANSFER_FIELD_BINDINGS: tuple[DesignerFieldBinding, ...] = (
    DesignerFieldBinding("sku", "SKU", "{{Item.SKU}}"),
    DesignerFieldBinding("name", "Item Name", "{{Item.Name}}"),
    DesignerFieldBinding("lot_number", "Lot / Batch", "{{Transfer.LotNumber}}"),
    DesignerFieldBinding("quantity", "Quantity", "{{Transfer.Quantity}}"),
    DesignerFieldBinding("unit", "Unit", "{{Transfer.Unit}}"),
    DesignerFieldBinding("from_location", "From Location", "{{Transfer.FromLocation}}"),
    DesignerFieldBinding("to_location", "To Location", "{{Transfer.ToLocation}}"),
    DesignerFieldBinding("reference", "Reference", "{{Transfer.Reference}}"),
    DesignerFieldBinding("person", "Person", "{{Transfer.Person}}"),
    DesignerFieldBinding("timestamp", "Timestamp", "{{Transfer.Timestamp}}"),
)


TRANSFER_SAMPLE_CONTEXT = {
    "Item": {
        "SKU": TRANSFER_SAMPLE_DATA["sku"],
        "Name": TRANSFER_SAMPLE_DATA["name"],
        "Description": TRANSFER_SAMPLE_DATA["name"],
    },
    "Transfer": {
        "LotNumber": TRANSFER_SAMPLE_DATA["lot_number"],
        "Quantity": TRANSFER_SAMPLE_DATA["quantity"],
        "Unit": TRANSFER_SAMPLE_DATA["unit"],
        "FromLocation": TRANSFER_SAMPLE_DATA["from_location"],
        "ToLocation": TRANSFER_SAMPLE_DATA["to_location"],
        "Reference": TRANSFER_SAMPLE_DATA["reference"],
        "Person": TRANSFER_SAMPLE_DATA["person"],
        "Timestamp": TRANSFER_SAMPLE_DATA["timestamp"],
    },
}


TRANSFER_DEFAULT_FIELDS = [
    {
        "id": "field-transfer-title",
        "label": "Transfer Label",
        "bindingKey": None,
        "type": "text",
        "x": 60,
        "y": 40,
        "width": 692,
        "height": 64,
        "rotation": 0,
        "fontSize": 48,
        "align": "center",
    },
    {
        "id": "field-transfer-sku",
        "label": "SKU",
        "bindingKey": "sku",
        "type": "text",
        "x": 60,
        "y": 140,
        "width": 692,
        "height": 50,
        "rotation": 0,
        "fontSize": 40,
        "align": "left",
    },
    {
        "id": "field-transfer-name",
        "label": "Item Name",
        "bindingKey": "name",
        "type": "text",
        "x": 60,
        "y": 200,
        "width": 692,
        "height": 46,
        "rotation": 0,
        "fontSize": 32,
        "align": "left",
    },
    {
        "id": "field-transfer-lot",
        "label": "Lot / Batch",
        "bindingKey": "lot_number",
        "type": "text",
        "x": 60,
        "y": 260,
        "width": 360,
        "height": 40,
        "rotation": 0,
        "fontSize": 28,
        "align": "left",
    },
    {
        "id": "field-transfer-qty",
        "label": "Qty",
        "bindingKey": "quantity",
        "type": "text",
        "x": 440,
        "y": 260,
        "width": 140,
        "height": 40,
        "rotation": 0,
        "fontSize": 28,
        "align": "left",
    },
    {
        "id": "field-transfer-unit",
        "label": "Unit",
        "bindingKey": "unit",
        "type": "text",
        "x": 600,
        "y": 260,
        "width": 152,
        "height": 40,
        "rotation": 0,
        "fontSize": 28,
        "align": "left",
    },
    {
        "id": "field-transfer-from",
        "label": "From",
        "bindingKey": "from_location",
        "type": "text",
        "x": 60,
        "y": 320,
        "width": 330,
        "height": 36,
        "rotation": 0,
        "fontSize": 26,
        "align": "left",
    },
    {
        "id": "field-transfer-to",
        "label": "To",
        "bindingKey": "to_location",
        "type": "text",
        "x": 420,
        "y": 320,
        "width": 332,
        "height": 36,
        "rotation": 0,
        "fontSize": 26,
        "align": "left",
    },
    {
        "id": "field-transfer-reference",
        "label": "Reference",
        "bindingKey": "reference",
        "type": "text",
        "x": 60,
        "y": 370,
        "width": 692,
        "height": 36,
        "rotation": 0,
        "fontSize": 24,
        "align": "left",
    },
    {
        "id": "field-transfer-person",
        "label": "Person",
        "bindingKey": "person",
        "type": "text",
        "x": 60,
        "y": 420,
        "width": 360,
        "height": 36,
        "rotation": 0,
        "fontSize": 24,
        "align": "left",
    },
    {
        "id": "field-transfer-timestamp",
        "label": "Timestamp",
        "bindingKey": "timestamp",
        "type": "text",
        "x": 420,
        "y": 420,
        "width": 332,
        "height": 36,
        "rotation": 0,
        "fontSize": 24,
        "align": "left",
    },
    {
        "id": "field-transfer-barcode",
        "label": "Item Barcode",
        "bindingKey": "sku",
        "type": "barcode",
        "x": 80,
        "y": 470,
        "width": 652,
        "height": 200,
        "rotation": 0,
        "fontSize": 18,
        "align": "center",
        "showValue": True,
    },
]


TRANSFER_DEFAULT_LAYOUT = {
    "id": "transfer-label",
    "name": "Transfer Label",
    "description": "Inventory move label with from/to location details.",
    "size": {"width": LABEL_WIDTH, "height": 700},
    "dataFields": _build_data_field_list(TRANSFER_FIELD_BINDINGS),
    "sampleData": dict(TRANSFER_SAMPLE_DATA),
    "fields": deepcopy(TRANSFER_DEFAULT_FIELDS),
}


BATCH_LABEL_CONFIG = DesignerLabelConfig(
    id="batch-label",
    name="Batch Label",
    description="Detailed batch label with full traceability data and scan-ready barcode.",
    template_name="LotBatchLabelTemplate",
    process="BatchCreated",
    data_fields=BATCH_FIELD_BINDINGS,
    sample_data=dict(BATCH_SAMPLE_DATA),
    sample_context=deepcopy(BATCH_SAMPLE_CONTEXT),
    default_layout=deepcopy(BATCH_DEFAULT_LAYOUT),
)


ORDER_LABEL_CONFIG = DesignerLabelConfig(
    id="order-label",
    name="Order Completion Label",
    description="Order completion summary label.",
    template_name="OrderCompletionLabelTemplate",
    process="OrderCompleted",
    data_fields=ORDER_FIELD_BINDINGS,
    sample_data=dict(ORDER_SAMPLE_DATA),
    sample_context=deepcopy(ORDER_SAMPLE_CONTEXT),
    default_layout=deepcopy(ORDER_DEFAULT_LAYOUT),
)


LOCATION_LABEL_CONFIG = DesignerLabelConfig(
    id="location-label",
    name="Location Label",
    description="Barcode label for fixed storage locations.",
    template_name="InventoryLocationLabelTemplate",
    process="LocationLabel",
    data_fields=LOCATION_FIELD_BINDINGS,
    sample_data=dict(LOCATION_SAMPLE_DATA),
    sample_context=deepcopy(LOCATION_SAMPLE_CONTEXT),
    default_layout=deepcopy(LOCATION_DEFAULT_LAYOUT),
)

ITEM_LABEL_CONFIG = DesignerLabelConfig(
    id="item-label",
    name="Item Label",
    description="Label for identifying inventory items by SKU.",
    template_name="InventoryItemLabelTemplate",
    process="ItemLabel",
    data_fields=ITEM_FIELD_BINDINGS,
    sample_data=dict(ITEM_SAMPLE_DATA),
    sample_context=deepcopy(ITEM_SAMPLE_CONTEXT),
    default_layout=deepcopy(ITEM_DEFAULT_LAYOUT),
)


TRANSFER_LABEL_CONFIG = DesignerLabelConfig(
    id="transfer-label",
    name="Transfer Label",
    description="Label for recording inventory moves between locations.",
    template_name="InventoryTransferLabelTemplate",
    process="InventoryTransferLabel",
    data_fields=TRANSFER_FIELD_BINDINGS,
    sample_data=dict(TRANSFER_SAMPLE_DATA),
    sample_context=deepcopy(TRANSFER_SAMPLE_CONTEXT),
    default_layout=deepcopy(TRANSFER_DEFAULT_LAYOUT),
)


DESIGNER_LABELS: dict[str, DesignerLabelConfig] = {
    BATCH_LABEL_CONFIG.id: BATCH_LABEL_CONFIG,
    ORDER_LABEL_CONFIG.id: ORDER_LABEL_CONFIG,
    LOCATION_LABEL_CONFIG.id: LOCATION_LABEL_CONFIG,
    ITEM_LABEL_CONFIG.id: ITEM_LABEL_CONFIG,
    TRANSFER_LABEL_CONFIG.id: TRANSFER_LABEL_CONFIG,
}

CONFIG_BY_TEMPLATE = {config.template_name: config for config in DESIGNER_LABELS.values()}
CONFIG_BY_PROCESS = {config.process: config for config in DESIGNER_LABELS.values()}


def get_designer_label_config(label_id: str) -> DesignerLabelConfig | None:
    return DESIGNER_LABELS.get(label_id)


def get_designer_label_for_template(template_name: str) -> DesignerLabelConfig | None:
    return CONFIG_BY_TEMPLATE.get(template_name)


def get_designer_label_for_process(process: str) -> DesignerLabelConfig | None:
    return CONFIG_BY_PROCESS.get(process)


def iter_designer_labels() -> tuple[DesignerLabelConfig, ...]:
    return tuple(DESIGNER_LABELS.values())


def get_designer_sample_context(label_id: str) -> Mapping[str, Any]:
    config = get_designer_label_config(label_id)
    if config is None:
        raise KeyError(f"Unknown designer label '{label_id}'.")
    return deepcopy(config.sample_context)


def _normalize_rotation(value: Any) -> int:
    try:
        degrees = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    degrees %= 360
    candidates = [0, 90, 180, 270]
    return min(candidates, key=lambda option: min(abs(option - degrees), 360 - abs(option - degrees)))


def _orientation_from_rotation(value: Any) -> str:
    normalized = _normalize_rotation(value)
    for orientation, degrees in ORIENTATION_TO_DEGREES.items():
        if degrees == normalized:
            return orientation
    return "N"


def _alignment_from_justify(value: Any) -> str:
    if isinstance(value, str):
        return JUSTIFY_TO_ALIGNMENT.get(value.upper(), "left")
    return "left"


def _justify_from_alignment(value: Any) -> str:
    if isinstance(value, str):
        return ALIGNMENT_TO_JUSTIFY.get(value.lower(), "L")
    return "L"


def serialize_designer_layout(label_id: str, layout: Mapping[str, Any]) -> dict[str, Any]:
    config = get_designer_label_config(label_id)
    if config is None:
        raise KeyError(f"Unknown designer label '{label_id}'.")

    size = layout.get("size") or {}
    width = int(size.get("width") or layout.get("width") or LABEL_WIDTH)
    height = int(size.get("height") or layout.get("height") or LABEL_HEIGHT)

    fields_map: dict[str, str] = {
        binding.key: binding.expression for binding in config.data_fields
    }

    elements: list[dict[str, Any]] = []
    for field in layout.get("fields", []):
        field_type = str(field.get("type") or "text").lower()
        rotation = _normalize_rotation(field.get("rotation", 0))
        orientation = _orientation_from_rotation(rotation)
        x = int(field.get("x") or 0)
        y = int(field.get("y") or 0)
        width_value = int(field.get("width") or 0)
        height_value = int(field.get("height") or 0)
        font_size = int(field.get("fontSize") or (height_value or 30))
        justify = _justify_from_alignment(field.get("align"))

        if field_type == "barcode":
            binding_key = field.get("bindingKey")
            if not binding_key:
                continue
            element = {
                "id": field.get("id"),
                "type": "barcode",
                "fieldKey": binding_key,
                "x": x,
                "y": y,
                "height": height_value or 180,
                "width": width_value or 0,
                "orientation": orientation,
                "printValue": field.get("showValue", True),
            }
            elements.append(element)
            fields_map.setdefault(binding_key, f"{{{{Data.{binding_key}}}}}")
            continue

        if field.get("bindingKey"):
            binding_key = field["bindingKey"]
            element = {
                "id": field.get("id"),
                "type": "field",
                "fieldKey": binding_key,
                "x": x,
                "y": y,
                "orientation": orientation,
                "fontSize": font_size,
                "maxWidth": width_value or 0,
                "maxLines": 1,
                "justify": justify,
            }
            if height_value:
                element["height"] = height_value
            elements.append(element)
            fields_map.setdefault(binding_key, f"{{{{Data.{binding_key}}}}}")
            continue

        element = {
            "id": field.get("id"),
            "type": "text",
            "text": field.get("label") or "",
            "x": x,
            "y": y,
            "orientation": orientation,
            "fontSize": font_size,
            "maxWidth": width_value or 0,
            "maxLines": 1,
            "justify": justify,
        }
        if height_value:
            element["height"] = height_value
        elements.append(element)

    layout_payload = {
        "width": width,
        "height": height,
        "elements": elements,
        "designerMeta": {
            "labelId": config.id,
            "uiSize": {"width": width, "height": height},
        },
    }

    return {"layout": layout_payload, "fields": fields_map}


def deserialize_designer_layout(
    label_id: str,
    layout: Mapping[str, Any],
    fields: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    config = get_designer_label_config(label_id)
    if config is None:
        raise KeyError(f"Unknown designer label '{label_id}'.")

    meta = layout.get("designerMeta") or {}
    ui_size = meta.get("uiSize") or {}
    width = int(ui_size.get("width") or layout.get("width") or LABEL_WIDTH)
    height = int(ui_size.get("height") or layout.get("height") or LABEL_HEIGHT)

    binding_labels = {binding.key: binding.label for binding in config.data_fields}

    ui_fields: list[dict[str, Any]] = []
    for index, element in enumerate(layout.get("elements", []), start=1):
        element_type = str(element.get("type") or "field").lower()
        rotation = ORIENTATION_TO_DEGREES.get(str(element.get("orientation", "N")).upper(), 0)
        base = {
            "id": element.get("id") or f"field-{index}",
            "x": int(element.get("x") or 0),
            "y": int(element.get("y") or 0),
            "width": int(element.get("maxWidth") or element.get("width") or 200),
            "height": int(element.get("height") or element.get("fontSize") or 40),
            "rotation": rotation,
            "align": _alignment_from_justify(element.get("justify")),
            "fontSize": int(element.get("fontSize") or element.get("height") or 30),
        }

        if element_type == "barcode":
            field_key = _field_key(element)
            if not field_key:
                continue
            field_dict = {
                **base,
                "type": "barcode",
                "bindingKey": field_key,
                "label": binding_labels.get(field_key, field_key),
                "showValue": element.get("printValue", True),
            }
            ui_fields.append(field_dict)
            continue

        if element_type == "text":
            field_dict = {
                **base,
                "type": "text",
                "bindingKey": None,
                "label": element.get("text") or "",
            }
            ui_fields.append(field_dict)
            continue

        field_key = _field_key(element)
        if not field_key:
            continue
        field_dict = {
            **base,
            "type": "text",
            "bindingKey": field_key,
            "label": binding_labels.get(field_key, field_key),
        }
        ui_fields.append(field_dict)

    return {
        "id": config.id,
        "name": config.name,
        "description": config.description,
        "size": {"width": width, "height": height},
        "dataFields": _build_data_field_list(config.data_fields),
        "sampleData": dict(config.sample_data),
        "fields": ui_fields,
    }


def build_designer_state(
    label_id: str,
    *,
    template_layout: Mapping[str, Any] | None = None,
    template_fields: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if template_layout:
        return deserialize_designer_layout(label_id, template_layout, template_fields)

    config = get_designer_label_config(label_id)
    if config is None:
        raise KeyError(f"Unknown designer label '{label_id}'.")
    return deepcopy(config.default_layout)


def _format_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    try:
        from datetime import date

        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
    except ImportError:  # pragma: no cover - fallback when datetime lacks date
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _format_timestamp(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    try:
        from datetime import date

        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
    except ImportError:  # pragma: no cover - fallback when datetime lacks date
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def build_batch_label_context(
    batch: "Batch" | Mapping[str, Any],
    *,
    item: "Item" | Mapping[str, Any] | None = None,
    quantity: int | None = None,
    location: "Location" | Mapping[str, Any] | None = None,
    po_number: str | None = None,
) -> dict[str, Any]:
    batch_obj = batch
    item_obj = item or getattr(batch_obj, "item", None)
    location_obj = location or getattr(batch_obj, "location", None)

    def _get(obj, attribute, default=""):
        if obj is None:
            return default
        if isinstance(obj, Mapping):
            return obj.get(attribute, default)
        return getattr(obj, attribute, default)

    lot_number = _get(batch_obj, "lot_number") or _get(batch_obj, "lotNumber", "")
    batch_quantity = quantity if quantity is not None else _get(batch_obj, "quantity", "")
    unit = _get(item_obj, "unit", "")
    expiration = _format_date(_get(batch_obj, "expiration_date", None))
    received = _format_date(_get(batch_obj, "received_date", None))
    supplier_name = _get(batch_obj, "supplier_name", "")
    supplier_code = _get(batch_obj, "supplier_code", "")
    purchase_order = po_number or _get(batch_obj, "purchase_order", "")
    notes = _get(batch_obj, "notes", "")

    item_name = _get(item_obj, "name", "")
    sku = _get(item_obj, "sku", "")
    description = _get(item_obj, "description", item_name)

    location_code = _get(location_obj, "code", "")

    return {
        "Batch": {
            "LotNumber": lot_number,
            "Quantity": batch_quantity,
            "Unit": unit,
            "ExpirationDate": expiration,
            "ReceivedDate": received,
            "SupplierName": supplier_name,
            "SupplierCode": supplier_code,
            "PurchaseOrder": purchase_order,
            "Notes": notes,
        },
        "Item": {
            "Name": item_name,
            "SKU": sku,
            "Description": description,
        },
        "Location": {"Code": location_code},
    }


def build_location_label_context(location: "Location" | Mapping[str, Any]) -> dict[str, Any]:
    def _get(obj, attribute, default=""):
        if obj is None:
            return default
        if isinstance(obj, Mapping):
            return obj.get(attribute, default)
        return getattr(obj, attribute, default)

    code = _get(location, "code", "")
    description = _get(location, "description", "")

    return {"Location": {"Code": code, "Description": description}}


def build_item_label_context(item: "Item" | Mapping[str, Any]) -> dict[str, Any]:
    def _get(obj, attribute, default=""):
        if obj is None:
            return default
        if isinstance(obj, Mapping):
            return obj.get(attribute, default)
        return getattr(obj, attribute, default)

    sku = _get(item, "sku", "")
    name = _get(item, "name", "")
    description = _get(item, "description", name)
    unit = _get(item, "unit", "")

    return {
        "Item": {
            "SKU": sku,
            "Name": name,
            "Description": description,
            "Unit": unit,
        }
    }


def build_transfer_label_context(
    item: "Item" | Mapping[str, Any],
    *,
    quantity: Any = None,
    batch: "Batch" | Mapping[str, Any] | None = None,
    from_location: "Location" | Mapping[str, Any] | None = None,
    to_location: "Location" | Mapping[str, Any] | None = None,
    reference: str | None = None,
    person: str | None = None,
    moved_at: Any = None,
) -> dict[str, Any]:
    def _get(obj, attribute, default=""):
        if obj is None:
            return default
        if isinstance(obj, Mapping):
            return obj.get(attribute, default)
        return getattr(obj, attribute, default)

    item_name = _get(item, "name", "")
    sku = _get(item, "sku", "")
    description = _get(item, "description", item_name)
    unit = _get(item, "unit", "")

    lot_number = _get(batch, "lot_number", "")
    from_code = _get(from_location, "code", "")
    to_code = _get(to_location, "code", "")

    return {
        "Item": {
            "SKU": sku,
            "Name": item_name,
            "Description": description,
            "Unit": unit,
        },
        "Transfer": {
            "LotNumber": lot_number,
            "Quantity": "" if quantity is None else quantity,
            "Unit": unit,
            "FromLocation": from_code,
            "ToLocation": to_code,
            "Reference": reference or "",
            "Person": person or "",
            "Timestamp": _format_timestamp(moved_at),
        },
    }


def register_label_definition(template: LabelDefinition, *, override: bool = True) -> None:
    """Register a ``LabelDefinition`` for runtime use."""

    if not override and template.name in LABEL_DEFINITIONS:
        raise ValueError(f"Label '{template.name}' is already registered.")
    LABEL_DEFINITIONS[template.name] = template
    for trigger in template.triggers:
        PROCESS_ASSIGNMENTS.setdefault(trigger, template.name)


def assign_template_to_process(process: str, template_name: str) -> None:
    """Explicitly map a process trigger to a label template name."""

    PROCESS_ASSIGNMENTS[process] = template_name


def get_template_by_name(template_name: str) -> LabelDefinition | None:
    template = LABEL_DEFINITIONS.get(template_name)
    if template is not None:
        return template
    db_template = _load_template_from_db(template_name)
    if db_template is not None:
        return db_template
    return None


def get_template_for_process(process: str) -> LabelDefinition | None:
    db_template = _load_template_from_db_for_process(process)
    if db_template is not None:
        return db_template
    template_name = PROCESS_ASSIGNMENTS.get(process)
    if not template_name:
        return None
    return get_template_by_name(template_name)


def render_template_by_name(template_name: str, context: Mapping[str, Any]) -> str:
    template = get_template_by_name(template_name)
    if template is None:
        raise KeyError(f"Unknown label template '{template_name}'.")
    return template.render(context)


def render_label_for_process(process: str, context: Mapping[str, Any]) -> str:
    template = get_template_for_process(process)
    if template is None:
        raise KeyError(f"No label template assigned to process '{process}'.")
    return template.render(context)


def build_receiving_label(
    batch_or_sku: Any,
    description: str | None = None,
    qty: int | None = None,
    *,
    item: Any | None = None,
    location: Any | None = None,
    po_number: str | None = None,
    lot_number: str | None = None,
) -> str:
    """Generate ZPL for a receiving label using the registered batch template."""

    if hasattr(batch_or_sku, "lot_number") or isinstance(batch_or_sku, Mapping):
        context = build_batch_label_context(
            batch_or_sku,
            item=item,
            quantity=qty,
            location=location,
            po_number=po_number,
        )
    else:
        sku = batch_or_sku or ""
        item_name = description or sku
        context = {
            "Batch": {
                "LotNumber": lot_number or sku,
                "Quantity": qty or 0,
                "Unit": getattr(item, "unit", "") if item is not None else "",
                "ExpirationDate": "",
                "ReceivedDate": "",
                "SupplierName": "",
                "SupplierCode": "",
                "PurchaseOrder": po_number or "",
                "Notes": "",
            },
            "Item": {
                "Name": item_name,
                "SKU": sku,
                "Description": description or item_name,
            },
            "Location": {
                "Code": getattr(location, "code", "") if location is not None else "",
            },
        }

    return render_label_for_process("BatchCreated", context)


def _resolve_fields(fields: Mapping[str, str], context: Mapping[str, Any]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, expression in fields.items():
        value = _evaluate_expression(expression, context)
        resolved[key] = _sanitize_zpl_text(value)
    return resolved


def _evaluate_expression(expression: Any, context: Mapping[str, Any]) -> Any:
    if expression is None:
        return ""
    if isinstance(expression, str):
        stripped = expression.strip()
        match = _PLACEHOLDER_PATTERN.fullmatch(stripped)
        if match and stripped == expression:
            path = [segment for segment in match.group(1).split(".") if segment]
            return _traverse_path(context, path)

        def _replace(match_obj: re.Match[str]) -> str:
            path = [segment for segment in match_obj.group(1).split(".") if segment]
            value = _traverse_path(context, path)
            return "" if value is None else str(value)

        return _PLACEHOLDER_PATTERN.sub(_replace, expression)
    return expression


def _traverse_path(value: Any, segments: list[str]) -> Any:
    current = value
    for segment in segments:
        if current is None:
            return ""
        if isinstance(current, Mapping):
            current = current.get(segment)
        else:
            current = getattr(current, segment, None)
    return "" if current is None else current


def _render_layout(layout: Mapping[str, Any], field_values: Mapping[str, str]) -> str:
    width = int(layout.get("width") or LABEL_WIDTH)
    height = int(layout.get("height") or LABEL_HEIGHT)
    commands = ["^XA", f"^PW{width}", f"^LL{height}"]

    for element in layout.get("elements", []):
        commands.extend(_render_element(element, field_values))

    commands.append("^XZ")
    return "\n".join(commands)


def _render_element(element: Mapping[str, Any], field_values: Mapping[str, str]) -> list[str]:
    element_type = str(element.get("type", "field")).lower()
    x = int(element.get("x", 0))
    y = int(element.get("y", 0))
    commands: list[str] = []

    if element_type in {"text", "field"}:
        if element_type == "text":
            text = str(element.get("text", ""))
        else:
            field_key = _field_key(element)
            value = field_values.get(field_key, "")
            text = f"{element.get('prefix', '')}{value}{element.get('suffix', '')}"
        if element.get("uppercase"):
            text = text.upper()
        text = _sanitize_zpl_text(text)
        justify = str(element.get("justify") or element.get("alignment") or "L").upper()[:1]
        max_width = int(element.get("maxWidth") or element.get("width") or 0)
        max_lines = int(element.get("maxLines") or 1)
        line_spacing = int(element.get("lineSpacing") or 0)
        pieces = [f"^FO{x},{y}{_font_command(element)}"]
        if max_width > 0:
            pieces.append(f"^FB{max_width},{max_lines},{line_spacing},{justify},0")
        pieces.append(f"^FD{text}^FS")
        commands.append("".join(pieces))
        return commands

    if element_type == "barcode":
        field_key = _field_key(element)
        value = _sanitize_zpl_text(field_values.get(field_key, ""))
        height = int(element.get("height") or element.get("barHeight") or 120)
        orientation = str(element.get("orientation", "N")).upper()[:1] or "N"
        print_text = "Y" if element.get("printValue", True) else "N"
        check_digit = "Y" if element.get("checkDigit", False) else "N"
        commands.append(
            f"^FO{x},{y}^BC{orientation},{height},{print_text},N,{check_digit}^FD{value}^FS"
        )
        return commands

    if element_type == "box":
        width = int(element.get("width", 0))
        height = int(element.get("height", 0))
        thickness = int(element.get("thickness", 2))
        commands.append(f"^FO{x},{y}^GB{width},{height},{thickness},B,0^FS")
        return commands

    return commands


def _font_command(element: Mapping[str, Any]) -> str:
    font = element.get("font") if isinstance(element.get("font"), Mapping) else {}
    name = (font.get("name") if isinstance(font, Mapping) else None) or element.get("fontName") or "0"
    orientation = (
        (font.get("orientation") if isinstance(font, Mapping) else None)
        or element.get("orientation")
        or "N"
    )
    height = int(
        (font.get("size") if isinstance(font, Mapping) else None)
        or element.get("fontSize")
        or element.get("height")
        or 30
    )
    width = (
        (font.get("width") if isinstance(font, Mapping) else None)
        or element.get("fontWidth")
    )
    orientation = str(orientation).upper()[:1] or "N"
    if width is not None:
        return f"^A{name},{orientation},{height},{int(width)}"
    return f"^A{name},{orientation},{height}"


def _field_key(element: Mapping[str, Any]) -> str | None:
    if isinstance(element.get("fieldKey"), str):
        return element["fieldKey"]
    binding = element.get("dataBinding")
    if isinstance(binding, Mapping) and isinstance(binding.get("fieldKey"), str):
        return binding["fieldKey"]
    if isinstance(element.get("field"), str):
        return element["field"]
    return None


def _sanitize_zpl_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("^", r"\^").replace("~", r"\~")


def _load_template_from_db(template_name: str) -> LabelDefinition | None:
    if not has_app_context():
        return None
    if "sqlalchemy" not in current_app.extensions:
        return None
    try:
        from invapp.models import LabelTemplate
    except ImportError:
        return None
    record = LabelTemplate.query.filter_by(name=template_name).first()
    if record is None:
        return None
    return LabelDefinition(
        name=record.name,
        layout=record.layout or {},
        fields=record.fields or {},
        description=record.description,
        triggers=(record.trigger,) if record.trigger else (),
    )


def _load_template_from_db_for_process(process: str) -> LabelDefinition | None:
    if not has_app_context():
        return None
    if "sqlalchemy" not in current_app.extensions:
        return None
    try:
        from invapp.models import LabelProcessAssignment, LabelTemplate
    except ImportError:
        return None
    assignment = (
        LabelProcessAssignment.query.join(LabelTemplate)
        .filter(LabelProcessAssignment.process == process)
        .first()
    )
    if assignment is None:
        return None
    template = assignment.template
    return LabelDefinition(
        name=template.name,
        layout=template.layout or {},
        fields=template.fields or {},
        description=template.description,
        triggers=(template.trigger,) if template.trigger else (),
    )


for designer_config in iter_designer_labels():
    serialized = serialize_designer_layout(designer_config.id, designer_config.default_layout)
    definition = LabelDefinition(
        name=designer_config.template_name,
        layout=serialized["layout"],
        fields=serialized["fields"],
        description=designer_config.description,
        triggers=(designer_config.process,),
    )
    register_label_definition(definition)
    assign_template_to_process(designer_config.process, definition.name)


__all__ = [
    "LabelDefinition",
    "assign_template_to_process",
    "build_batch_label_context",
    "build_item_label_context",
    "build_location_label_context",
    "build_transfer_label_context",
    "build_designer_state",
    "build_receiving_label",
    "deserialize_designer_layout",
    "get_designer_label_config",
    "get_designer_label_for_process",
    "get_designer_label_for_template",
    "get_designer_sample_context",
    "get_template_by_name",
    "get_template_for_process",
    "iter_designer_labels",
    "register_label_definition",
    "render_label_for_process",
    "render_template_by_name",
    "serialize_designer_layout",
]
