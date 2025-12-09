import importlib.util
from pathlib import Path
import sys

# Load the labels module without importing the full invapp package
module_path = Path(__file__).resolve().parents[1] / "invapp" / "printing" / "labels.py"
spec = importlib.util.spec_from_file_location("labels", module_path)
labels = importlib.util.module_from_spec(spec)
sys.modules.setdefault("labels", labels)
spec.loader.exec_module(labels)


def test_build_receiving_label_generates_expected_zpl():
    label = labels.build_receiving_label("ABC123", "Widget", 5)
    assert label.startswith("^XA")
    assert "^BC" in label
    assert "ABC123" in label
    assert "Widget" in label


def test_render_label_for_batch_created_matches_builder():
    batch = {"lot_number": "LOT-123", "quantity": 5, "purchase_order": "PO-7"}
    item = {"sku": "ABC123", "name": "Widget", "description": "Widget"}
    location = {"code": "RCV-01"}
    context = labels.build_batch_label_context(batch, item=item, quantity=5, location=location, po_number="PO-7")
    rendered = labels.render_label_for_process("BatchCreated", context)
    assert rendered == labels.build_receiving_label(batch, qty=5, item=item, location=location, po_number="PO-7")


def test_order_completion_template_includes_order_details():
    context = {
        "Order": {
            "ID": "WO-42",
            "CustomerName": "Horizon Builders",
            "Address": "991 Market Street",
            "CityState": "San Francisco, CA",
            "DueDate": "2024-06-01",
            "ItemID": "GATE-AL-42",
        }
    }
    zpl = labels.render_label_for_process("OrderCompleted", context)
    assert "^FO40,110" in zpl
    assert "Order #WO-42" in zpl
    assert "Customer: Horizon Builders" in zpl
    assert "Item: GATE-AL-42" in zpl


def test_custom_template_can_be_registered_and_assigned():
    template = labels.LabelDefinition(
        name="TestTemplate",
        layout={
            "width": 200,
            "height": 120,
            "elements": [
                {
                    "type": "field",
                    "fieldKey": "demo.value",
                    "x": 10,
                    "y": 10,
                    "fontSize": 20,
                }
            ],
        },
        fields={"demo.value": "{{Value}}"},
    )
    labels.register_label_definition(template)
    labels.assign_template_to_process("DemoEvent", template.name)
    try:
        rendered = labels.render_label_for_process("DemoEvent", {"Value": "Hello"})
    finally:
        labels.LABEL_DEFINITIONS.pop(template.name, None)
        labels.PROCESS_ASSIGNMENTS.pop("DemoEvent", None)
    assert "Hello" in rendered


def test_location_label_template_renders_location_details():
    context = labels.build_location_label_context(
        {"code": "LOC-01", "description": "North Wall"}
    )
    zpl = labels.render_label_for_process("LocationLabel", context)
    assert "LOC-01" in zpl
    assert "North Wall" in zpl
    assert "^BC" in zpl

