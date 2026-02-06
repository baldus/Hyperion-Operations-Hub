import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp.utils.location_code import aisle_from_location_code, normalize_aisle_key


def test_aisle_from_location_code_uses_second_hyphen_token():
    assert aisle_from_location_code("1-CTRL-12") == "CTRL"
    assert aisle_from_location_code("2-g-07") == "G"
    assert aisle_from_location_code(" 3-Controllers-9 ") == "CONTROLLERS"


def test_aisle_from_location_code_invalid_values():
    assert aisle_from_location_code(None) is None
    assert aisle_from_location_code("") is None
    assert aisle_from_location_code("A") is None
    assert aisle_from_location_code("1--1") is None


def test_normalize_aisle_key():
    assert normalize_aisle_key(" locks ") == "LOCKS"
    assert normalize_aisle_key("  ") is None
