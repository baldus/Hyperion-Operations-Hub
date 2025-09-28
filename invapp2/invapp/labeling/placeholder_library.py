"""Placeholder label definitions for the Operations label designer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class DataField:
    key: str
    label: str
    sample: str | None = None
    type: str = "text"


@dataclass(frozen=True)
class DataSource:
    id: str
    name: str
    description: str
    fields: List[DataField]


@dataclass(frozen=True)
class FieldDefinition:
    id: str
    label: str
    key: str | None
    type: str
    position: Dict[str, float]
    size: Dict[str, float]
    rotation: float = 0
    style: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanvasDefinition:
    width: float
    height: float
    unit: str = "px"
    background_color: str = "#ffffff"


@dataclass(frozen=True)
class LabelDefinition:
    id: str
    name: str
    description: str
    canvas: CanvasDefinition
    data_sources: List[DataSource]
    fields: List[FieldDefinition]

    def to_payload(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "canvas": {
                "width": self.canvas.width,
                "height": self.canvas.height,
                "unit": self.canvas.unit,
                "backgroundColor": self.canvas.background_color,
            },
            "dataSources": [
                {
                    "id": source.id,
                    "name": source.name,
                    "description": source.description,
                    "fields": [
                        {
                            "key": field.key,
                            "label": field.label,
                            "sample": field.sample,
                            "type": field.type,
                        }
                        for field in source.fields
                    ],
                }
                for source in self.data_sources
            ],
            "fields": [
                {
                    "id": field.id,
                    "label": field.label,
                    "key": field.key,
                    "type": field.type,
                    "position": field.position,
                    "size": field.size,
                    "rotation": field.rotation,
                    "style": field.style,
                }
                for field in self.fields
            ],
        }


PLACEHOLDER_LABELS: List[LabelDefinition] = [
    LabelDefinition(
        id="inventory_item",
        name="Inventory Item Shelf Tag",
        description="Bin label summarising stock identity and storage requirements.",
        canvas=CanvasDefinition(width=520, height=300, unit="px", background_color="#f8fafc"),
        data_sources=[
            DataSource(
                id="item",
                name="Inventory Item",
                description="Core attributes supplied by the item catalogue.",
                fields=[
                    DataField(key="item.name", label="Item name", sample="Widget 3000"),
                    DataField(key="item.sku", label="Item SKU", sample="SKU-3492"),
                    DataField(key="item.description", label="Item description", sample="High efficiency hydraulic fitting"),
                    DataField(key="item.unit", label="Unit of measure", sample="Each"),
                ],
            ),
            DataSource(
                id="stock_location",
                name="Primary location",
                description="Preferred storage location and capacity guidance.",
                fields=[
                    DataField(key="location.code", label="Location code", sample="Aisle 12 • Bin 4"),
                    DataField(key="location.capacity", label="Bin capacity", sample="Max 120 units"),
                    DataField(key="location.notes", label="Handling notes", sample="Keep dry • Inspect weekly"),
                ],
            ),
        ],
        fields=[
            FieldDefinition(
                id="title",
                label="Item name",
                key="item.name",
                type="text",
                position={"x": 32, "y": 28},
                size={"width": 320, "height": 64},
                style={
                    "fontSize": 28,
                    "fontWeight": 600,
                    "textAlign": "left",
                    "color": "#0f172a",
                    "backgroundColor": "rgba(255,255,255,0.9)",
                    "borderColor": "rgba(148,163,184,0.35)",
                    "borderWidth": 1,
                },
            ),
            FieldDefinition(
                id="sku",
                label="Item SKU",
                key="item.sku",
                type="text",
                position={"x": 32, "y": 110},
                size={"width": 220, "height": 40},
                style={
                    "fontSize": 18,
                    "fontWeight": 500,
                    "textAlign": "left",
                    "color": "#1e293b",
                    "backgroundColor": "rgba(255,255,255,0.85)",
                    "borderColor": "rgba(148,163,184,0.25)",
                    "borderWidth": 1,
                },
            ),
            FieldDefinition(
                id="location",
                label="Location code",
                key="location.code",
                type="text",
                position={"x": 32, "y": 168},
                size={"width": 260, "height": 48},
                style={
                    "fontSize": 20,
                    "fontWeight": 600,
                    "textAlign": "left",
                    "color": "#0369a1",
                    "backgroundColor": "rgba(191,219,254,0.25)",
                    "borderColor": "rgba(59,130,246,0.4)",
                    "borderWidth": 1,
                },
            ),
            FieldDefinition(
                id="notes",
                label="Handling notes",
                key="location.notes",
                type="text",
                position={"x": 32, "y": 232},
                size={"width": 320, "height": 48},
                style={
                    "fontSize": 14,
                    "fontWeight": 500,
                    "textAlign": "left",
                    "color": "#0f172a",
                    "backgroundColor": "rgba(148,163,184,0.2)",
                    "borderColor": "rgba(148,163,184,0.35)",
                    "borderWidth": 1,
                },
            ),
            FieldDefinition(
                id="icon",
                label="Safety badge",
                key=None,
                type="text",
                position={"x": 380, "y": 36},
                size={"width": 120, "height": 120},
                style={
                    "fontSize": 36,
                    "fontWeight": 700,
                    "textAlign": "center",
                    "color": "#f8fafc",
                    "backgroundColor": "#ef4444",
                    "borderColor": "rgba(239,68,68,0.6)",
                    "borderWidth": 1,
                },
            ),
        ],
    ),
    LabelDefinition(
        id="outbound_shipment",
        name="Outbound Shipment Carton",
        description="4×6 label used for outbound parcel manifests and staging.",
        canvas=CanvasDefinition(width=600, height=400, unit="px", background_color="#ffffff"),
        data_sources=[
            DataSource(
                id="shipment",
                name="Shipment",
                description="Data populated when the carton is packed for dispatch.",
                fields=[
                    DataField(key="shipment.number", label="Shipment number", sample="SO-2025-00428"),
                    DataField(key="shipment.customer", label="Customer", sample="Atlas Manufacturing"),
                    DataField(key="shipment.route", label="Route", sample="LTL • Dock 3"),
                    DataField(key="shipment.destination", label="Destination", sample="Denver, CO"),
                    DataField(key="shipment.weight", label="Total weight", sample="48.6 lb"),
                ],
            ),
            DataSource(
                id="order",
                name="Order",
                description="Linked order references.",
                fields=[
                    DataField(key="order.number", label="Order number", sample="PO-993883"),
                    DataField(key="order.reference", label="Reference", sample="Install lot 6"),
                ],
            ),
        ],
        fields=[
            FieldDefinition(
                id="shipment-number",
                label="Shipment number",
                key="shipment.number",
                type="text",
                position={"x": 36, "y": 32},
                size={"width": 320, "height": 60},
                style={
                    "fontSize": 26,
                    "fontWeight": 700,
                    "textAlign": "left",
                    "color": "#0f172a",
                    "backgroundColor": "rgba(224,231,255,0.6)",
                    "borderColor": "rgba(99,102,241,0.45)",
                    "borderWidth": 2,
                },
            ),
            FieldDefinition(
                id="customer",
                label="Customer",
                key="shipment.customer",
                type="text",
                position={"x": 36, "y": 112},
                size={"width": 360, "height": 52},
                style={
                    "fontSize": 20,
                    "fontWeight": 600,
                    "textAlign": "left",
                    "color": "#1f2937",
                    "backgroundColor": "rgba(209,213,219,0.35)",
                    "borderColor": "rgba(156,163,175,0.4)",
                    "borderWidth": 1,
                },
            ),
            FieldDefinition(
                id="destination",
                label="Destination",
                key="shipment.destination",
                type="text",
                position={"x": 36, "y": 184},
                size={"width": 360, "height": 52},
                style={
                    "fontSize": 20,
                    "fontWeight": 600,
                    "textAlign": "left",
                    "color": "#0369a1",
                    "backgroundColor": "rgba(191,219,254,0.35)",
                    "borderColor": "rgba(14,165,233,0.4)",
                    "borderWidth": 1,
                },
            ),
            FieldDefinition(
                id="route",
                label="Route",
                key="shipment.route",
                type="text",
                position={"x": 36, "y": 252},
                size={"width": 320, "height": 48},
                style={
                    "fontSize": 18,
                    "fontWeight": 600,
                    "textAlign": "left",
                    "color": "#111827",
                    "backgroundColor": "rgba(244,244,245,0.8)",
                    "borderColor": "rgba(148,163,184,0.35)",
                    "borderWidth": 1,
                },
            ),
            FieldDefinition(
                id="weight",
                label="Weight",
                key="shipment.weight",
                type="text",
                position={"x": 36, "y": 316},
                size={"width": 200, "height": 48},
                style={
                    "fontSize": 18,
                    "fontWeight": 600,
                    "textAlign": "left",
                    "color": "#047857",
                    "backgroundColor": "rgba(16,185,129,0.18)",
                    "borderColor": "rgba(16,185,129,0.4)",
                    "borderWidth": 1,
                },
            ),
            FieldDefinition(
                id="order",
                label="Order number",
                key="order.number",
                type="text",
                position={"x": 392, "y": 112},
                size={"width": 180, "height": 48},
                style={
                    "fontSize": 18,
                    "fontWeight": 600,
                    "textAlign": "center",
                    "color": "#1f2937",
                    "backgroundColor": "rgba(229,231,235,0.6)",
                    "borderColor": "rgba(148,163,184,0.45)",
                    "borderWidth": 1,
                },
            ),
            FieldDefinition(
                id="reference",
                label="Reference",
                key="order.reference",
                type="text",
                position={"x": 392, "y": 168},
                size={"width": 180, "height": 48},
                style={
                    "fontSize": 16,
                    "fontWeight": 500,
                    "textAlign": "center",
                    "color": "#312e81",
                    "backgroundColor": "rgba(199,210,254,0.4)",
                    "borderColor": "rgba(129,140,248,0.45)",
                    "borderWidth": 1,
                },
            ),
        ],
    ),
]


def get_placeholder_payload() -> List[Dict[str, Any]]:
    """Return the placeholder labels as JSON-serialisable dictionaries."""
    return [label.to_payload() for label in PLACEHOLDER_LABELS]


__all__ = ["PLACEHOLDER_LABELS", "get_placeholder_payload"]
