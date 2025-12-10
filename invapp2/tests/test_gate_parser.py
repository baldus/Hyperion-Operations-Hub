import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp.gate_parser import GatePartNumberError, parse_gate_part_number


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


def test_parse_vinyl_gate_with_adders():
    parsed = parse_gate_part_number("CYNL81CK80FCB")

    assert parsed.material == "Vinyl"
    assert parsed.panel_material_color == "Vinyl - Walnut"
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

