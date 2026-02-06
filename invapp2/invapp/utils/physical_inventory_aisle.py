from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Iterable

from invapp.utils.location_parser import normalize_row_key, parse_location_code


UNKNOWN_AISLE = "UNKNOWN"


def _normalize_aisle(value: object | None) -> str:
    row_key = normalize_row_key(None if value is None else str(value))
    if row_key is None:
        return UNKNOWN_AISLE
    return row_key


def get_location_aisle(location, app_config) -> str:
    """Return the aisle key for a Location based on configuration rules."""

    if location is None:
        return UNKNOWN_AISLE

    mode = (app_config.get("PHYS_INV_AISLE_MODE") or "row").lower()
    code = getattr(location, "code", None)

    if mode == "prefix":
        pattern = app_config.get("PHYS_INV_AISLE_REGEX")
        if not pattern or not code:
            return UNKNOWN_AISLE
        try:
            matcher = re.compile(pattern)
        except re.error:
            return UNKNOWN_AISLE
        match = matcher.search(str(code))
        if not match:
            return UNKNOWN_AISLE
        group_dict = match.groupdict()
        if "aisle" in group_dict:
            return _normalize_aisle(group_dict["aisle"])
        return _normalize_aisle(match.group(0))

    parsed = None
    if mode == "level":
        value = getattr(location, "level", None)
        if value is None and code:
            parsed = parse_location_code(code)
            value = parsed.level
    else:
        value = getattr(location, "row", None)
        if value is None and code:
            parsed = parse_location_code(code)
            value = parsed.row if parsed else None

    return _normalize_aisle(value)


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
