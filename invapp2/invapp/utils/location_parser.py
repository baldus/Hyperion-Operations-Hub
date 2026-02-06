from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from invapp.utils.location_code import aisle_from_location_code


@dataclass(frozen=True)
class ParsedLocation:
    level: Optional[int]
    row: Optional[str]
    bay: Optional[int]


def normalize_row_key(value: str | None) -> str | None:
    # Backward-compatible alias for callers still importing this helper.
    from invapp.utils.location_code import normalize_aisle_key

    return normalize_aisle_key(value)


def parse_location_code(code: str | None) -> ParsedLocation:
    if not code:
        return ParsedLocation(level=None, row=None, bay=None)

    parts = str(code).strip().split("-")
    if len(parts) < 3:
        return ParsedLocation(level=None, row=None, bay=None)

    level_raw = parts[0].strip()
    bay_raw = parts[2].strip()
    row = aisle_from_location_code(code)

    try:
        level = int(level_raw)
        bay = int(bay_raw)
    except ValueError:
        return ParsedLocation(level=None, row=None, bay=None)

    return ParsedLocation(level=level, row=row, bay=bay)
