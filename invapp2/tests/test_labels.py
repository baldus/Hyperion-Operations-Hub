import importlib.util
from pathlib import Path

# Load the labels module without importing the full invapp package
module_path = Path(__file__).resolve().parents[1] / "invapp" / "printing" / "labels.py"
spec = importlib.util.spec_from_file_location("labels", module_path)
labels = importlib.util.module_from_spec(spec)
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

