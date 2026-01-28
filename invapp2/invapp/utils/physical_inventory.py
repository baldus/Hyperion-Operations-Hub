from __future__ import annotations

import re
from typing import Optional

from flask import current_app

from invapp.models import Location
from invapp.utils.location_parser import parse_location_code


DEFAULT_AISLE_MODE = "row"
DEFAULT_AISLE_PREFIX_REGEX = r"^\s*([^-]+)"


def _safe_regex_group(match: re.Match | None) -> Optional[str]:
    if not match:
        return None
    if "aisle" in match.re.groupindex:
        return match.group("aisle")
    if match.groups():
        return match.group(1)
    return match.group(0) if match.group(0) else None


def _normalize_aisle_value(value: object | None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def get_location_aisle(
    location: Location | None,
    *,
    mode: str | None = None,
    regex: str | None = None,
) -> Optional[str]:
    """Derive an aisle label for a Location using configured conventions."""

    if location is None:
        return None

    resolved_mode = (mode or current_app.config.get("PHYS_INV_AISLE_MODE") or "").lower()
    if resolved_mode not in {"row", "level", "prefix"}:
        resolved_mode = DEFAULT_AISLE_MODE

    if resolved_mode == "prefix":
        pattern = regex or current_app.config.get("PHYS_INV_AISLE_REGEX")
        compiled = re.compile(pattern or DEFAULT_AISLE_PREFIX_REGEX)
        match = compiled.search(location.code or "")
        return _normalize_aisle_value(_safe_regex_group(match))

    parsed = parse_location_code(location.code)
    if resolved_mode == "level":
        return _normalize_aisle_value(location.level if location.level is not None else parsed.level)
    return _normalize_aisle_value(location.row if location.row is not None else parsed.row)
