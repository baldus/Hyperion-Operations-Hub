import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from invapp.models import Location
from invapp.utils.physical_inventory import get_location_aisle


def test_get_location_aisle_row_mode():
    location = Location(code="1-A-1")
    assert get_location_aisle(location, mode="row") == "A"


def test_get_location_aisle_prefix_mode_with_regex():
    location = Location(code="ZONE-12-B")
    regex = r"^(?P<aisle>[A-Za-z]+)"
    assert get_location_aisle(location, mode="prefix", regex=regex) == "ZONE"


def test_get_location_aisle_row_mode_fallback_none():
    location = Location(code="ROWLESS")
    assert get_location_aisle(location, mode="row") is None
