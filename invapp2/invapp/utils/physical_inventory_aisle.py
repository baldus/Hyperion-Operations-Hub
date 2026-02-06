from __future__ import annotations

from types import SimpleNamespace
from typing import Iterable

from invapp.utils.location_code import aisle_from_location_code
from invapp.utils.location_parser import parse_location_code


UNKNOWN_AISLE = "UNKNOWN"


def _normalize_aisle(value: object | None) -> str:
    if value is None:
        return UNKNOWN_AISLE
    text = str(value).strip()
    if not text:
        return UNKNOWN_AISLE
    return text


def get_location_aisle(location, app_config=None) -> str:
    """Return the aisle key for a Location derived from code token index 1."""

    if location is None:
        return UNKNOWN_AISLE

    code = getattr(location, "code", None)
    return _normalize_aisle(aisle_from_location_code(code))


def sort_aisle_keys(aisles: Iterable[str]) -> list[str]:
    def sort_key(value: str) -> tuple:
        normalized = (value or "").strip()
        if not normalized or normalized.upper() == UNKNOWN_AISLE:
            return (1, 0, "")
        try:
            return (0, 0, int(normalized))
        except ValueError:
            return (0, 1, normalized.upper())

    return sorted(aisles, key=sort_key)


def location_sort_key(code: str | None) -> tuple:
    if not code:
        return (1, float("inf"), "", float("inf"), "")
    parsed = parse_location_code(code)
    if parsed.level is None or parsed.row is None or parsed.bay is None:
        return (1, float("inf"), "", float("inf"), code.lower())
    return (0, parsed.level, parsed.row, parsed.bay, code.lower())


def make_location_stub(code: str | None):
    return SimpleNamespace(code=code)
