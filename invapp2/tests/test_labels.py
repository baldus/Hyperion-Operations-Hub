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
    expected = (
        "^XA\n"
        "^PW812\n"
        "^LL1218\n"
        "^FO50,50^A0,N,50^FDABC123^FS\n"
        "^FO50,110^A0,N,30^FDWidget^FS\n"
        "^FO50,170^A0,N,30^FDQty: 5^FS\n"
        "^FO50,230^BCN,100,Y,N,N^FDABC123^FS\n"
        "^XZ"
    )
    assert label == expected


def test_render_label_for_batch_created_matches_builder():
    context = {
        "Item": {"SKU": "ABC123", "Description": "Widget"},
        "Batch": {"Quantity": 5},
    }
    rendered = labels.render_label_for_process("BatchCreated", context)
    assert rendered == labels.build_receiving_label("ABC123", "Widget", 5)


def test_order_completion_template_includes_order_details():
    context = {
        "Order": {
            "ID": "WO-42",
            "CustomerName": "Horizon Builders",
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

