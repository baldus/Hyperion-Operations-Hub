import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp.utils.location_parser import normalize_row_key, parse_location_code


def test_parse_location_code_valid():
    parsed = parse_location_code("1-A-1")
    assert parsed.level == 1
    assert parsed.row == "A"
    assert parsed.bay == 1


def test_parse_location_code_leading_zeros_and_case():
    parsed = parse_location_code("01-a-12")
    assert parsed.level == 1
    assert parsed.row == "A"
    assert parsed.bay == 12


def test_parse_location_code_whitespace():
    parsed = parse_location_code(" 2 - B - 03 ")
    assert parsed.level == 2
    assert parsed.row == "B"
    assert parsed.bay == 3


def test_parse_location_code_invalid():
    parsed = parse_location_code("A-1")
    assert parsed.level is None
    assert parsed.row is None
    assert parsed.bay is None

    parsed = parse_location_code("1A1")
    assert parsed.level is None
    assert parsed.row is None
    assert parsed.bay is None

    parsed = parse_location_code("")
    assert parsed.level is None
    assert parsed.row is None
    assert parsed.bay is None


def test_parse_location_code_multiletter_row():
    parsed = parse_location_code("1-slctr-1")
    assert parsed.level == 1
    assert parsed.row == "SLCTR"
    assert parsed.bay == 1


def test_normalize_row_key():
    assert normalize_row_key(" g ") == "G"
    assert normalize_row_key("Controllers") == "CONTROLLERS"
    assert normalize_row_key("  SL  CTR  ") == "SL CTR"
    assert normalize_row_key("") is None
    assert normalize_row_key(None) is None
