from __future__ import annotations


def normalize_upper_trim(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


def aisle_from_location_code(code: str | None) -> str | None:
    if code is None:
        return None

    parts = code.strip().split("-")
    if len(parts) < 3:
        return None

    aisle_raw = parts[1].strip()
    if aisle_raw == "":
        return None

    return aisle_raw.upper()
