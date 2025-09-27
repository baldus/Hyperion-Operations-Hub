"""Label template registry and ZPL builders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping

LABEL_WIDTH = 812  # dots for 4" width at 203 DPI
LABEL_HEIGHT = 1218  # dots for 6" height at 203 DPI

# Lot/batch label element positions (x, y) in dots
SKU_TEXT_ORIGIN = (50, 50)
DESC_TEXT_ORIGIN = (50, 110)
QTY_TEXT_ORIGIN = (50, 170)
BARCODE_ORIGIN = (50, 230)

# Order completion label positions
ORDER_HEADER_ORIGIN = (60, 50)
ORDER_NUMBER_ORIGIN = (60, 140)
CUSTOMER_TEXT_ORIGIN = (60, 220)
ITEM_TEXT_ORIGIN = (60, 300)
ORDER_BARCODE_ORIGIN = (60, 380)


class LabelTemplateError(RuntimeError):
    """Raised when a label template or process mapping cannot be resolved."""


@dataclass(frozen=True)
class LabelTemplate:
    """Represents a label template definition."""

    name: str
    trigger: str
    field_map: Mapping[str, str]
    builder: Callable[[Mapping[str, str]], str]

    def render(self, context: Mapping[str, Any]) -> str:
        resolved = resolve_field_map(self.field_map, context)
        return self.builder(resolved)


def resolve_field_map(field_map: Mapping[str, str], context: Mapping[str, Any]) -> Dict[str, str]:
    return {key: resolve_field_value(value, context) for key, value in field_map.items()}


def resolve_field_value(expression: str | None, context: Mapping[str, Any]) -> str:
    if expression is None:
        return ""
    expression = str(expression)
    if expression.startswith("{{") and expression.endswith("}}"):
        path = expression[2:-2].strip()
        if not path:
            return ""
        value: Any = context
        for part in path.split('.'):
            if isinstance(value, Mapping):
                value = value.get(part)
            else:
                value = getattr(value, part, None)
            if value is None:
                break
        return "" if value is None else str(value)
    return expression


def build_lot_batch_label(fields: Mapping[str, str]) -> str:
    sku = fields.get("Sku", "")
    description = fields.get("Description", "")
    quantity = fields.get("Quantity", "")
    barcode = fields.get("Barcode", sku)
    lines = [
        "^XA",
        f"^PW{LABEL_WIDTH}",
        f"^LL{LABEL_HEIGHT}",
        f"^FO{SKU_TEXT_ORIGIN[0]},{SKU_TEXT_ORIGIN[1]}^A0,N,50^FD{sku}^FS",
        f"^FO{DESC_TEXT_ORIGIN[0]},{DESC_TEXT_ORIGIN[1]}^A0,N,30^FD{description}^FS",
        f"^FO{QTY_TEXT_ORIGIN[0]},{QTY_TEXT_ORIGIN[1]}^A0,N,30^FDQty: {quantity}^FS",
        f"^FO{BARCODE_ORIGIN[0]},{BARCODE_ORIGIN[1]}^BCN,100,Y,N,N^FD{barcode}^FS",
        "^XZ",
    ]
    return "\n".join(lines)


def build_order_completion_label(fields: Mapping[str, str]) -> str:
    order_number = fields.get("OrderNumber", "")
    customer = fields.get("Customer", "")
    item_number = fields.get("ItemNumber", "")
    item_description = fields.get("ItemDescription", "")
    lines = [
        "^XA",
        f"^PW{LABEL_WIDTH}",
        f"^LL{LABEL_HEIGHT}",
        f"^FO{ORDER_HEADER_ORIGIN[0]},{ORDER_HEADER_ORIGIN[1]}^A0,N,60^FDOrder Complete^FS",
        f"^FO{ORDER_NUMBER_ORIGIN[0]},{ORDER_NUMBER_ORIGIN[1]}^A0,N,48^FDOrder #: {order_number}^FS",
        f"^FO{CUSTOMER_TEXT_ORIGIN[0]},{CUSTOMER_TEXT_ORIGIN[1]}^A0,N,40^FDCustomer: {customer}^FS",
        f"^FO{ITEM_TEXT_ORIGIN[0]},{ITEM_TEXT_ORIGIN[1]}^A0,N,36^FDItem: {item_number} {item_description}^FS",
        f"^FO{ORDER_BARCODE_ORIGIN[0]},{ORDER_BARCODE_ORIGIN[1]}^BCN,120,Y,N,N^FD{order_number}^FS",
        "^XZ",
    ]
    return "\n".join(lines)


class LabelRegistry:
    """In-memory registry that maps processes to templates."""

    def __init__(self) -> None:
        self._templates: Dict[str, LabelTemplate] = {}
        self._process_map: Dict[str, str] = {}

    def register_template(self, template: LabelTemplate) -> None:
        self._templates[template.name] = template

    def assign_template(self, process: str, template_name: str) -> None:
        if template_name not in self._templates:
            raise LabelTemplateError(f"Unknown label template: {template_name}")
        self._process_map[process] = template_name

    def get_template(self, template_name: str) -> LabelTemplate:
        try:
            return self._templates[template_name]
        except KeyError as exc:  # pragma: no cover - defensive branch
            raise LabelTemplateError(f"Unknown label template: {template_name}") from exc

    def template_for_process(self, process: str) -> LabelTemplate:
        try:
            template_name = self._process_map[process]
        except KeyError as exc:
            raise LabelTemplateError(f"No label template assigned to process '{process}'.") from exc
        return self.get_template(template_name)

    def render_for_process(self, process: str, context: Mapping[str, Any]) -> str:
        template = self.template_for_process(process)
        return template.render(context)


registry = LabelRegistry()

LOT_BATCH_TEMPLATE = LabelTemplate(
    name="LotBatchLabelTemplate",
    trigger="BatchCreated",
    field_map={
        "Sku": "{{Batch.Item.SKU}}",
        "Description": "{{Batch.Item.Description}}",
        "Quantity": "{{Batch.Quantity}}",
        "Barcode": "{{Batch.Item.SKU}}",
    },
    builder=build_lot_batch_label,
)

ORDER_COMPLETION_TEMPLATE = LabelTemplate(
    name="OrderCompletionLabelTemplate",
    trigger="OrderCompleted",
    field_map={
        "OrderNumber": "{{Order.Number}}",
        "Customer": "{{Order.CustomerName}}",
        "ItemNumber": "{{Item.SKU}}",
        "ItemDescription": "{{Item.Description}}",
    },
    builder=build_order_completion_label,
)

registry.register_template(LOT_BATCH_TEMPLATE)
registry.register_template(ORDER_COMPLETION_TEMPLATE)
registry.assign_template(LOT_BATCH_TEMPLATE.trigger, LOT_BATCH_TEMPLATE.name)
registry.assign_template(ORDER_COMPLETION_TEMPLATE.trigger, ORDER_COMPLETION_TEMPLATE.name)


def get_label_template(template_name: str) -> LabelTemplate:
    return registry.get_template(template_name)


def assign_template_to_process(process: str, template_name: str) -> None:
    registry.assign_template(process, template_name)


def get_template_for_process(process: str) -> LabelTemplate:
    return registry.template_for_process(process)


def render_label_for_process(process: str, context: Mapping[str, Any]) -> str:
    return registry.render_for_process(process, context)


def build_receiving_label(sku: str, description: str, qty: int) -> str:
    """Generate a batch label for a received item."""

    context = {
        "Batch": {
            "Quantity": qty,
            "Item": {
                "SKU": sku,
                "Description": description,
            },
        }
    }
    return render_label_for_process("BatchCreated", context)
