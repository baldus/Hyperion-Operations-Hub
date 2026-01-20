from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


_LOCATION_CODE_PATTERN = re.compile(r"^\s*(\d+)\s*-\s*([A-Za-z]+)\s*-\s*(\d+)\s*$")


@dataclass(frozen=True)
class ParsedLocation:
    level: Optional[int]
    row: Optional[str]
    bay: Optional[int]


def parse_location_code(code: str | None) -> ParsedLocation:
    if not code:
        return ParsedLocation(level=None, row=None, bay=None)

    match = _LOCATION_CODE_PATTERN.match(code)
    if not match:
        return ParsedLocation(level=None, row=None, bay=None)

    level_raw, row_raw, bay_raw = match.groups()
    try:
        level = int(level_raw)
        bay = int(bay_raw)
    except ValueError:
        return ParsedLocation(level=None, row=None, bay=None)

    return ParsedLocation(level=level, row=row_raw.upper(), bay=bay)
