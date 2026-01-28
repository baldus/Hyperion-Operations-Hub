import os
import sys
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item
from invapp.services.physical_inventory import (
    NormalizationOptions,
    get_item_text_fields,
    match_upload_rows,
)


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


def test_match_by_item_name(app):
    with app.app_context():
        item = Item(sku="SKU-1", name="Widget A")
        db.session.add(item)
        db.session.commit()

        rows = [{"Item Name": "Widget A", "Qty": "5"}]
        options = NormalizationOptions()
        result = match_upload_rows(
            rows,
            primary_upload_column="Item Name",
            primary_item_field="name",
            quantity_column="Qty",
            options=options,
        )

        assert result["matched_count"] == 1
        assert result["unmatched_count"] == 0
        assert result["ambiguous_count"] == 0
        assert result["matched_rows"][0]["item_id"] == item.id
        assert result["matched_rows"][0]["quantity"] == Decimal("5")


def test_normalization_options(app):
    with app.app_context():
        item = Item(sku="SKU-2", name="Widget A")
        db.session.add(item)
        db.session.commit()

        rows = [{"Item Name": " widget-a ", "Qty": "1"}]
        options = NormalizationOptions(
            trim_whitespace=True,
            case_insensitive=True,
            remove_spaces=True,
            remove_dashes_underscores=True,
        )
        result = match_upload_rows(
            rows,
            primary_upload_column="Item Name",
            primary_item_field="name",
            quantity_column="Qty",
            options=options,
        )

        assert result["matched_count"] == 1


def test_ambiguous_detection(app):
    with app.app_context():
        db.session.add_all([
            Item(sku="SKU-3", name="Gizmo"),
            Item(sku="SKU-4", name="Gizmo"),
        ])
        db.session.commit()

        rows = [{"Item Name": "Gizmo", "Qty": "2"}]
        result = match_upload_rows(
            rows,
            primary_upload_column="Item Name",
            primary_item_field="name",
            quantity_column="Qty",
            options=NormalizationOptions(),
        )

        assert result["ambiguous_count"] == 1
        assert result["matched_count"] == 0


def test_unmatched_handling(app):
    with app.app_context():
        db.session.add(Item(sku="SKU-5", name="Known"))
        db.session.commit()

        rows = [{"Item Name": "Unknown", "Qty": "3"}]
        result = match_upload_rows(
            rows,
            primary_upload_column="Item Name",
            primary_item_field="name",
            quantity_column="Qty",
            options=NormalizationOptions(),
        )

        assert result["unmatched_count"] == 1
        assert result["matched_count"] == 0


def test_sku_excluded_from_item_fields(app):
    with app.app_context():
        field_names = {field["name"] for field in get_item_text_fields()}
        assert "sku" not in field_names
