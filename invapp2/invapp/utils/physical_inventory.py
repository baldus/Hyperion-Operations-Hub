"""Helpers for physical inventory aisle derivation."""

from __future__ import annotations

import re
from typing import Optional

from flask import current_app

from invapp.models import Location
from invapp.utils.location_parser import parse_location_code


DEFAULT_AISLE_MODE = "row"


def _normalize_aisle(value: object | None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.upper()


def _extract_by_regex(code: str | None, pattern: str | None) -> Optional[str]:
    if not code or not pattern:
        return None
    match = re.search(pattern, code)
    if not match:
        return None
    if "aisle" in match.groupdict():
        return _normalize_aisle(match.group("aisle"))
    if match.groups():
        return _normalize_aisle(match.group(1))
    return _normalize_aisle(match.group(0))


def _segment_from_code(code: str | None, index: int) -> Optional[str]:
    if not code:
        return None
    parts = [part.strip() for part in code.split("-") if part.strip()]
    if len(parts) <= index:
        return None
    return _normalize_aisle(parts[index])


def get_location_aisle(
    location: Location | None,
    mode: str | None = None,
    regex: str | None = None,
) -> str:
    """Return the aisle identifier for a location.

    Mode can be "row", "level", or "prefix". If a regex is provided, the
    first match (or named group "aisle") is used before mode-based fallback.
    """

    if location is None:
        return "UNLOCATED"

    aisle_mode = mode or current_app.config.get("PHYS_INV_AISLE_MODE", DEFAULT_AISLE_MODE)
    aisle_regex = regex or current_app.config.get("PHYS_INV_AISLE_REGEX")

    regex_value = _extract_by_regex(location.code, aisle_regex)
    if regex_value:
        return regex_value

    if aisle_mode == "level":
        parsed = parse_location_code(location.code)
        return _normalize_aisle(parsed.level) or _segment_from_code(location.code, 0) or "UNLOCATED"

    if aisle_mode == "prefix":
        return _segment_from_code(location.code, 0) or "UNLOCATED"

    parsed = parse_location_code(location.code)
    return _normalize_aisle(parsed.row) or _segment_from_code(location.code, 1) or _segment_from_code(location.code, 0) or "UNLOCATED"
