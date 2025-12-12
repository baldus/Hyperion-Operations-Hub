import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp import create_app
from invapp.extensions import db
from invapp.gate_parser import GatePartNumberError, parse_gate_part_number


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": "superuser", "password": "joshbaldus"},
        follow_redirects=True,
    )
    return client


def test_parse_acrylic_gate():
    parsed = parse_gate_part_number("BSR700N780")

    assert parsed.material == "Acrylic"
    assert parsed.panel_material_color == "Acrylic - Bronze"
    assert parsed.handing == "RH Center Pin"
    assert parsed.panel_count == 7
    assert parsed.vision_panel_qty == 0
    assert parsed.vision_panel_color == "None"
    assert parsed.hardware_option == "Nickle"
    assert parsed.door_height_inches == 78
    assert parsed.adders == []


def test_parse_slim_hardwood_gate_with_adders():
    parsed = parse_gate_part_number("CYNL81CK80FCB")

    assert parsed.material == "Slim Hardwood"
    assert parsed.panel_material_color == "Walnut (Hardwood, No Finish)"
    assert parsed.handing == "LH Center Pin"
    assert parsed.panel_count == 8
    assert parsed.vision_panel_qty == 1
    assert parsed.vision_panel_color == "Clear"
    assert parsed.hardware_option == "Black"
    assert pytest.approx(parsed.door_height_inches, rel=1e-5) == 80.375
    assert parsed.adders == ["Cambridge"]


def test_parse_metal_gate_multiple_adders():
    parsed = parse_gate_part_number("MBF82R482MADB")

    assert parsed.material.startswith("Metal")
    assert parsed.panel_material_color == "Oblong Perforated Aluminum"
    assert parsed.handing == "Offset Pin Double Lead Post"
    assert parsed.panel_count == 8
    assert parsed.vision_panel_qty == 2
    assert parsed.vision_panel_color == "Round Perforated"
    assert parsed.hardware_option == "Black"
    assert parsed.door_height_display.startswith("82 ")
    assert parsed.adders == ["Gate Arm", "Dark Brown Barrels"]


def test_parse_no_vision_panels():
    parsed = parse_gate_part_number("MSL600B800")

    assert parsed.vision_panel_qty == 0
    assert parsed.vision_panel_color == "None"
    assert parsed.door_height_inches == 80


def test_even_panel_validation_for_double_lead():
    with pytest.raises(GatePartNumberError):
        parse_gate_part_number("BSE700B780")


def test_material_codes_decode_correctly():
    parsed_d = parse_gate_part_number("DKR700N780")
    assert parsed_d.material == "Vinyl"
    assert parsed_d.panel_material_color == "Vinyl - Black"

    parsed_dy = parse_gate_part_number("DYKR700N780")
    assert parsed_dy.material == "Slim Vinyl"
    assert parsed_dy.panel_material_color == "Slim Vinyl - Black"

    parsed_cy = parse_gate_part_number("CYNR700N780")
    assert parsed_cy.material == "Slim Hardwood"
    assert parsed_cy.panel_material_color == "Walnut (Hardwood, No Finish)"

    parsed_by = parse_gate_part_number("BYSR700N780")
    assert parsed_by.material == "Slim Acrylic"
    assert parsed_by.panel_material_color == "Slim Acrylic - Bronze"

    parsed_my = parse_gate_part_number("MYSR700N780")
    assert parsed_my.material == "Slim Metal (Aluminum)"
    assert parsed_my.panel_material_color == "Slim Solid Aluminum"


def test_parse_gate_part_number_api_returns_materials(client):
    response_vinyl = client.post(
        "/orders/api/parse_gate_part_number", json={"part_number": "DKR700N780"}
    )
    assert response_vinyl.status_code == 200
    assert response_vinyl.get_json()["material"] == "Vinyl"

    response_slim_hardwood = client.post(
        "/orders/api/parse_gate_part_number", json={"part_number": "CYNR700N780"}
    )
    assert response_slim_hardwood.status_code == 200
    assert response_slim_hardwood.get_json()["material"] == "Slim Hardwood"

