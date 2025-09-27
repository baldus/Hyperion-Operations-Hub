"""Label template registry and ZPL rendering utilities."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from flask import current_app, has_app_context

LABEL_WIDTH = 812  # dots for 4" width at 203 DPI
LABEL_HEIGHT = 1218  # dots for 6" height at 203 DPI

_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([^}]+)\s*\}\}")


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


LABEL_DEFINITIONS: dict[str, LabelDefinition] = {}
PROCESS_ASSIGNMENTS: dict[str, str] = {}


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


def build_receiving_label(sku: str, description: str, qty: int) -> str:
    """Generate ZPL for a receiving label using the registered batch template."""

    context = {
        "Item": {"SKU": sku, "Description": description},
        "Batch": {"Quantity": qty},
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
        match = _PLACEHOLDER_PATTERN.fullmatch(expression.strip())
        if match:
            path = [segment for segment in match.group(1).split(".") if segment]
            return _traverse_path(context, path)
        return expression
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

    if element_type == "text":
        text = str(element.get("text", ""))
        if element.get("uppercase"):
            text = text.upper()
        text = _sanitize_zpl_text(text)
        commands.append(f"^FO{x},{y}{_font_command(element)}^FD{text}^FS")
        return commands

    if element_type == "field":
        field_key = _field_key(element)
        value = field_values.get(field_key, "")
        text = f"{element.get('prefix', '')}{value}{element.get('suffix', '')}"
        if element.get("uppercase"):
            text = text.upper()
        text = _sanitize_zpl_text(text)
        commands.append(f"^FO{x},{y}{_font_command(element)}^FD{text}^FS")
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


LOT_BATCH_DEFINITION = LabelDefinition(
    name="LotBatchLabelTemplate",
    description="Default lot/batch receiving label.",
    layout={
        "width": LABEL_WIDTH,
        "height": LABEL_HEIGHT,
        "elements": [
            {
                "id": "sku",
                "type": "field",
                "fieldKey": "inventory.item.sku",
                "x": 50,
                "y": 50,
                "fontSize": 50,
                "fontWeight": "700",
            },
            {
                "id": "description",
                "type": "field",
                "fieldKey": "inventory.item.description",
                "x": 50,
                "y": 110,
                "fontSize": 30,
            },
            {
                "id": "quantity",
                "type": "field",
                "fieldKey": "inventory.stock.quantity",
                "x": 50,
                "y": 170,
                "fontSize": 30,
                "prefix": "Qty: ",
            },
            {
                "id": "barcode",
                "type": "barcode",
                "fieldKey": "inventory.item.sku",
                "x": 50,
                "y": 230,
                "height": 100,
            },
        ],
    },
    fields={
        "inventory.item.sku": "{{Item.SKU}}",
        "inventory.item.description": "{{Item.Description}}",
        "inventory.stock.quantity": "{{Batch.Quantity}}",
    },
    triggers=("BatchCreated",),
)


ORDER_COMPLETION_DEFINITION = LabelDefinition(
    name="OrderCompletionLabelTemplate",
    description="Order completion summary label.",
    layout={
        "width": LABEL_WIDTH,
        "height": 700,
        "elements": [
            {
                "id": "title",
                "type": "text",
                "text": "Order completion",
                "x": 40,
                "y": 30,
                "fontSize": 36,
                "fontWeight": "700",
                "uppercase": True,
            },
            {
                "id": "order-number",
                "type": "field",
                "fieldKey": "orders.order.number",
                "x": 40,
                "y": 110,
                "fontSize": 48,
                "prefix": "Order #",
            },
            {
                "id": "customer",
                "type": "field",
                "fieldKey": "orders.customer.name",
                "x": 40,
                "y": 180,
                "fontSize": 34,
                "prefix": "Customer: ",
            },
            {
                "id": "item",
                "type": "field",
                "fieldKey": "orders.item.number",
                "x": 40,
                "y": 240,
                "fontSize": 34,
                "prefix": "Item: ",
            },
            {
                "id": "order-barcode",
                "type": "barcode",
                "fieldKey": "orders.order.number",
                "x": 40,
                "y": 320,
                "height": 140,
            },
        ],
    },
    fields={
        "orders.order.number": "{{Order.ID}}",
        "orders.customer.name": "{{Order.CustomerName}}",
        "orders.item.number": "{{Order.ItemID}}",
    },
    triggers=("OrderCompleted",),
)


register_label_definition(LOT_BATCH_DEFINITION)
register_label_definition(ORDER_COMPLETION_DEFINITION)
assign_template_to_process("BatchCreated", LOT_BATCH_DEFINITION.name)
assign_template_to_process("OrderCompleted", ORDER_COMPLETION_DEFINITION.name)


__all__ = [
    "LabelDefinition",
    "assign_template_to_process",
    "build_receiving_label",
    "get_template_by_name",
    "get_template_for_process",
    "register_label_definition",
    "render_label_for_process",
    "render_template_by_name",
]
