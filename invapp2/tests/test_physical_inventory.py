import io
import os
import sys
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item
from invapp.physical_inventory import services


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app_with_part_override():
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "PHYS_INV_ITEM_ID_FIELDS": "sku",
            "PHYS_INV_DESC_FIELDS": "name,description",
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_parse_import_csv(app):
    with app.app_context():
        csv_text = "Part Number,Quantity\nPN-100,5\n"
        parsed = services.parse_import_file("snapshot.csv", io.BytesIO(csv_text.encode("utf-8")))
        assert parsed.normalized_headers == ["part_number", "quantity"]
        assert parsed.rows[0]["part_number"] == "PN-100"


def test_parse_import_xlsx(app):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    with app.app_context():
        wb = Workbook()
        ws = wb.active
        ws.append(["Part Number", "Quantity"])
        ws.append(["PN-200", 7])
        stream = io.BytesIO()
        wb.save(stream)
        stream.seek(0)

        parsed = services.parse_import_file("snapshot.xlsx", stream)
        assert parsed.normalized_headers == ["part_number", "quantity"]
        assert parsed.rows[0]["part_number"] == "PN-200"


def test_suggest_quantity_column(app):
    with app.app_context():
        rows = [
            {"part": "A", "qty": "10", "name": "Widget"},
            {"part": "B", "qty": "5", "name": "Gadget"},
        ]
        suggestion = services.suggest_quantity_column(["part", "qty", "name"], rows)
        assert suggestion == "qty"


def test_match_by_part_number_exact_and_normalized(app_with_part_override):
    with app_with_part_override.app_context():
        item = Item(sku="PN-100", name="Widget")
        db.session.add(item)
        db.session.commit()

        candidates = services.get_item_field_candidates()
        rows = [
            {"part": "PN-100"},
            {"part": "PN 100"},
        ]
        context = services.match_items(rows, "part", None, candidates)
        assert context.matches[0].match_reason == "part_number_exact"
        assert context.matches[1].match_reason == "part_number_normalized"


def test_match_disambiguation_part_desc(app):
    app.config.update(
        PHYS_INV_ITEM_ID_FIELDS="item_class",
        PHYS_INV_DESC_FIELDS="name",
    )
    with app.app_context():
        item_one = Item(sku="SKU-1", name="Widget A", item_class="WIDGET")
        item_two = Item(sku="SKU-2", name="Widget B", item_class="WIDGET")
        db.session.add_all([item_one, item_two])
        db.session.commit()

        candidates = services.get_item_field_candidates()
        rows = [{"part": "WIDGET", "desc": "Widget B"}]
        context = services.match_items(rows, "part", "desc", candidates)
        assert context.matches[0].match_reason == "part+desc"
        assert context.matches[0].item_id == item_two.id


def test_duplicate_grouping_sum(app):
    with app.app_context():
        rows = [
            {"part": "PN-1", "qty": "1"},
            {"part": "PN-1", "qty": "2"},
        ]
        grouped, summary = services.group_duplicate_rows(
            rows,
            part_col="part",
            desc_col=None,
            quantity_col="qty",
            strategy="sum",
        )
        assert summary.duplicate_groups == 1
        assert grouped[0]["qty"] == str(Decimal("3"))


def test_candidate_fields_exclude_sku(app):
    with app.app_context():
        candidates = services.get_item_field_candidates()
        assert "sku" not in [field.lower() for field in candidates.part_number_fields]
