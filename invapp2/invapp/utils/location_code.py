from __future__ import annotations


def normalize_aisle_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split()).strip()
    if not normalized:
        return None
    return normalized.upper()


def aisle_from_location_code(code: str | None) -> str | None:
    if code is None:
        return None
    trimmed = str(code).strip()
    if not trimmed:
        return None
    parts = trimmed.split("-")
    if len(parts) < 3:
        return None
    aisle_raw = parts[1].strip()
    if not aisle_raw:
        return None
    return normalize_aisle_key(aisle_raw)
