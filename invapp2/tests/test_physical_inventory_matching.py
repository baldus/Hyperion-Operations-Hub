import os
import sys
from decimal import Decimal

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.models import Item
import invapp.routes.inventory as inventory
from flask import render_template

from invapp.services.physical_inventory import (
    NormalizationOptions,
    aggregate_matched_rows,
    build_missing_item_candidates,
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


def test_match_field_dropdown_excludes_sku_in_template(app):
    with app.app_context():
        with app.test_request_context():
            html = render_template(
                "inventory/physical_inventory_mapping.html",
                headers=["Item Name"],
                sample_rows=[],
                import_token="token",
                selected_mappings={},
                item_fields=get_item_text_fields(),
                duplicate_strategies={"sum": "Sum duplicate quantities"},
                selected_primary_item_field="name",
                selected_secondary_item_field="description",
                source_filename="test.csv",
                options={
                    "trim_whitespace": True,
                    "case_insensitive": True,
                    "remove_spaces": False,
                    "remove_dashes_underscores": False,
                },
                duplicate_strategy="sum",
                create_missing_items=False,
            )
            assert "Item.sku" not in html


def test_create_missing_items_flow(app):
    with app.app_context():
        db.session.add(Item(sku="SKU-10", name="Widget A", description="Existing"))
        db.session.commit()

    with app.app_context():
        rows = [
            {"Item Name": "Widget A", "Description": "Existing", "Qty": "5"},
            {"Item Name": "Widget New", "Description": "New desc", "Qty": "3"},
        ]
        options = NormalizationOptions()
        match_results = match_upload_rows(
            rows,
            primary_upload_column="Item Name",
            primary_item_field="name",
            quantity_column="Qty",
            secondary_upload_column="Description",
            secondary_item_field="description",
            options=options,
        )
        candidates = build_missing_item_candidates(
            match_results["unmatched_rows"],
            primary_upload_column="Item Name",
            secondary_upload_column="Description",
            secondary_item_field="description",
            options=options,
        )
        created_items = inventory._create_missing_inventory_items(
            candidates,
            primary_item_field="name",
            options=options,
            request_ip=None,
        )
        db.session.flush()

        updated_results = match_upload_rows(
            rows,
            primary_upload_column="Item Name",
            primary_item_field="name",
            quantity_column="Qty",
            secondary_upload_column="Description",
            secondary_item_field="description",
            options=options,
        )
        totals = aggregate_matched_rows(updated_results["matched_rows"], "sum")

        snapshot = inventory.PhysicalInventorySnapshot(
            primary_upload_column="Item Name",
            primary_item_field="name",
            secondary_upload_column="Description",
            secondary_item_field="description",
            quantity_column="Qty",
            normalization_options=options.to_dict(),
            duplicate_strategy="sum",
            total_rows=updated_results["total_rows"],
            matched_rows=updated_results["matched_count"],
            unmatched_rows=updated_results["unmatched_count"],
            ambiguous_rows=updated_results["ambiguous_count"],
            created_items=created_items,
        )
        db.session.add(snapshot)
        db.session.flush()
        for item_id, quantity in totals.items():
            db.session.add(
                inventory.PhysicalInventorySnapshotLine(
                    snapshot_id=snapshot.id, item_id=item_id, erp_quantity=quantity
                )
            )
        db.session.commit()

        created = Item.query.filter_by(name="Widget New").one_or_none()
        assert created is not None
        assert snapshot.created_items == 1
        assert len(snapshot.lines) == 2
