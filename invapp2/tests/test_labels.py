import importlib.util
import sys
from pathlib import Path

# Load the labels module without importing the full invapp package
module_path = Path(__file__).resolve().parents[1] / "invapp" / "printing" / "labels.py"
spec = importlib.util.spec_from_file_location("labels", module_path)
labels = importlib.util.module_from_spec(spec)
sys.modules["labels"] = labels
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


def test_render_label_for_batch_process_matches_expected_zpl():
    context = {
        "Batch": {
            "Item": {"SKU": "ABC123", "Description": "Widget"},
            "Quantity": 5,
        }
    }
    label = labels.render_label_for_process("BatchCreated", context)
    expected = labels.build_receiving_label("ABC123", "Widget", 5)
    assert label == expected


def test_render_order_completion_label_generates_expected_zpl():
    context = {
        "Order": {"Number": "ORD-001", "CustomerName": "Acme Builders"},
        "Item": {"SKU": "IT-42", "Description": "Aluminum Gate"},
    }
    label = labels.render_label_for_process("OrderCompleted", context)
    expected = (
        "^XA\n"
        "^PW812\n"
        "^LL1218\n"
        "^FO60,50^A0,N,60^FDOrder Complete^FS\n"
        "^FO60,140^A0,N,48^FDOrder #: ORD-001^FS\n"
        "^FO60,220^A0,N,40^FDCustomer: Acme Builders^FS\n"
        "^FO60,300^A0,N,36^FDItem: IT-42 Aluminum Gate^FS\n"
        "^FO60,380^BCN,120,Y,N,N^FDORD-001^FS\n"
        "^XZ"
    )
    assert label == expected


def test_resolve_field_value_supports_attribute_paths():
    class Item:
        def __init__(self, sku):
            self.SKU = sku

    context = {"Batch": {"Item": Item("ABC123")}}
    resolved = labels.resolve_field_value("{{Batch.Item.SKU}}", context)
    assert resolved == "ABC123"

