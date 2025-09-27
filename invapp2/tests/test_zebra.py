import importlib.util
from pathlib import Path
import types
import sys


flask_stub = types.ModuleType("flask")


class Flask:
    def __init__(self, name):
        self.config = {}
        self.logger = types.SimpleNamespace(error=lambda *a, **k: None)

    class _AppCtx:
        def __init__(self, app):
            self.app = app

        def __enter__(self):
            flask_stub.current_app = self.app
            return self.app

        def __exit__(self, exc_type, exc, tb):
            flask_stub.current_app = None

    def app_context(self):
        return Flask._AppCtx(self)


flask_stub.Flask = Flask
flask_stub.current_app = None
sys.modules["flask"] = flask_stub

from flask import Flask


def test_zebra_helpers_send_and_render_labels(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[1] / "invapp" / "printing" / "zebra.py"
    )
    spec = importlib.util.spec_from_file_location("zebra", module_path)
    zebra = importlib.util.module_from_spec(spec)

    app = Flask(__name__)
    app.config["ZEBRA_PRINTER_HOST"] = "printer.local"
    app.config["ZEBRA_PRINTER_PORT"] = 9101

    with app.app_context():
        invapp_pkg = types.ModuleType("invapp")
        invapp_pkg.__path__ = []
        printing_pkg = types.ModuleType("invapp.printing")
        printing_pkg.__path__ = [str(module_path.parent)]
        sys.modules.setdefault("invapp", invapp_pkg)
        sys.modules.setdefault("invapp.printing", printing_pkg)

        labels_path = module_path.parent / "labels.py"
        labels_spec = importlib.util.spec_from_file_location(
            "invapp.printing.labels", labels_path
        )
        labels_module = importlib.util.module_from_spec(labels_spec)
        sys.modules["invapp.printing.labels"] = labels_module
        labels_spec.loader.exec_module(labels_module)

        zebra.__package__ = "invapp.printing"
        spec.loader.exec_module(zebra)

        sent = []

        class DummySocket:
            def __init__(self, addr):
                self.addr = addr

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def sendall(self, data):
                sent.append((self.addr, data))

        def fake_create_connection(addr):
            return DummySocket(addr)

        monkeypatch.setattr(zebra.socket, "create_connection", fake_create_connection)

        result = zebra.print_receiving_label("ABC123", "Widget", 5)
        process_result = zebra.print_label_for_process(
            "OrderCompleted",
            {
                "Order": {"Number": "ORD-001", "CustomerName": "Acme"},
                "Item": {"SKU": "IT-42", "Description": "Widget"},
            },
        )

        captured_request = {}

        class DummyResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def read(self):
                return b"PNGDATA"

        def fake_urlopen(request):
            captured_request["url"] = request.full_url
            captured_request["data"] = request.data
            captured_request["headers"] = dict(request.header_items())
            return DummyResponse()

        monkeypatch.setattr(zebra, "urlopen", fake_urlopen)

        png_bytes = zebra.render_label_png_for_process(
            "BatchCreated",
            {
                "Batch": {
                    "Quantity": 5,
                    "Item": {"SKU": "ABC123", "Description": "Widget"},
                }
            },
            dpi="12dpmm",
            size="4x6",
            index=1,
        )

    batch_expected = zebra.build_receiving_label("ABC123", "Widget", 5)
    order_expected = labels_module.render_label_for_process(
        "OrderCompleted",
        {
            "Order": {"Number": "ORD-001", "CustomerName": "Acme"},
            "Item": {"SKU": "IT-42", "Description": "Widget"},
        },
    )

    assert result is True
    assert process_result is True
    assert sent == [
        (("printer.local", 9101), batch_expected.encode("utf-8")),
        (("printer.local", 9101), order_expected.encode("utf-8")),
    ]
    assert png_bytes == b"PNGDATA"
    assert (
        captured_request["url"]
        == "http://api.labelary.com/v1/printers/12dpmm/labels/4x6/1/"
    )
    assert captured_request["data"] == batch_expected.encode("utf-8")
    assert captured_request["headers"].get("Accept") == "image/png"

