import importlib.util
from pathlib import Path
import types
import sys


flask_stub = types.ModuleType("flask")


class Flask:
    def __init__(self, name):
        self.config = {}
        self.logger = types.SimpleNamespace(error=lambda *a, **k: None)
        self.extensions = {}

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
flask_stub.has_app_context = lambda: flask_stub.current_app is not None
sys.modules["flask"] = flask_stub

from flask import Flask


def test_print_receiving_label_sends_zpl(monkeypatch):
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
        models_pkg = types.ModuleType("invapp.models")
        services_pkg = types.ModuleType("invapp.services")

        class DummyQuery:
            def filter_by(self, **kwargs):
                return self

            def order_by(self, *args, **kwargs):
                return self

            def all(self):
                return []

            def get(self, _id):
                return None

        class Printer:
            query = DummyQuery()

        class User:
            pass

        models_pkg.Printer = Printer
        models_pkg.User = User

        class DummyStatusBus:
            @staticmethod
            def log_event(*args, **kwargs):
                return None

        services_pkg.status_bus = DummyStatusBus()

        sys.modules.setdefault("invapp.models", models_pkg)
        sys.modules.setdefault("invapp.services", services_pkg)

        labels_path = module_path.parent / "labels.py"
        labels_spec = importlib.util.spec_from_file_location(
            "invapp.printing.labels", labels_path
        )
        labels_module = importlib.util.module_from_spec(labels_spec)
        sys.modules.setdefault("invapp.printing.labels", labels_module)
        labels_spec.loader.exec_module(labels_module)

        zebra.__package__ = "invapp.printing"
        spec.loader.exec_module(zebra)

        sent = {}

        class DummySocket:
            def __init__(self, addr):
                sent["addr"] = addr

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def sendall(self, data):
                sent["data"] = data

        def fake_create_connection(addr):
            return DummySocket(addr)

        monkeypatch.setattr(zebra.socket, "create_connection", fake_create_connection)

        result = zebra.print_receiving_label("ABC123", "Widget", 5)

    expected = zebra.build_receiving_label("ABC123", "Widget", 5)
    assert sent["addr"] == ("printer.local", 9101)
    assert sent["data"] == expected.encode("utf-8")
    assert result.ok is True
