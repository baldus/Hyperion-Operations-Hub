"""Utilities for decoding intelligent gate part numbers.

The mapping tables are derived from the provided specification. The parser is
designed to be tolerant of minor ambiguities (for example, the panel count code
`1` appearing twice in the spec) while still returning descriptive errors when
input cannot be interpreted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


class GatePartNumberError(ValueError):
    """Raised when a part number cannot be parsed."""


_TWO_CHAR_MATERIALS = {"BY", "CY", "DY", "MY"}

_MATERIAL_MAP: Dict[str, str] = {
    "A": "Not in use",
    "B": "Acrylic",
    "BY": "Slim Acrylic",
    "C": "Hardwood",
    "CY": "Slim Hardwood",
    "D": "Vinyl",
    "DY": "Slim Vinyl",
    "H": "No longer in use",
    "M": "Metal (Aluminum)",
    "MY": "Slim Metal (Aluminum)",
    "V": "No longer in use",
}

_HARDWOOD_PANEL_TYPES = {
    "A": "Alder (Hardwood, No Finish)",
    "B": "Birch (Hardwood, No Finish)",
    "C": "Cherry (Hardwood, No Finish)",
    "M": "Mahogany (Hardwood, No Finish)",
    "L": "Maple (Hardwood, No Finish)",
    "P": "Pine (Hardwood, No Finish)",
    "K": "Oak (Hardwood, No Finish)",
    "T": "Teak (Hardwood, No Finish)",
    "N": "Walnut (Hardwood, No Finish)",
    "W": "White Oak (Hardwood, No Finish)",
    "S": "Special Hardwood (No Finish)",
    # Clear Coat Hardwood uses the same material family but numeric codes
    # for colors, so we keep them in the same mapping block.
    "1": "Birch (Clear Coat Hardwood)",
    "2": "Cherry (Clear Coat Hardwood)",
    "3": "Mahogany (Clear Coat Hardwood)",
    "4": "Maple (Clear Coat Hardwood)",
    "5": "Oak (Clear Coat Hardwood)",
    "6": "Teak (Clear Coat Hardwood)",
    "7": "Walnut (Clear Coat Hardwood)",
    "8": "White Oak (Clear Coat Hardwood)",
    "9": "Alder (Clear Coat Hardwood)",
    "0": "Pine (Clear Coat Hardwood)",
}

_PANEL_TYPE_MAP: Dict[str, Dict[str, str]] = {
    "Acrylic": {
        "K": "Acrylic - Clear",
        "S": "Acrylic - Bronze",
        "F": "Acrylic - Frosted",
        "G": "Acrylic - Grey",
    },
    "Slim Acrylic": {
        "K": "Slim Acrylic - Clear",
        "S": "Slim Acrylic - Bronze",
        "F": "Slim Acrylic - Frosted",
        "G": "Slim Acrylic - Grey",
    },
    "Metal (Aluminum)": {
        "S": "Solid Aluminum",
        "B": "Oblong Perforated Aluminum",
        "R": "Round Perforated Aluminum",
        "Z": "Special Metal",
    },
    "Slim Metal (Aluminum)": {
        "S": "Slim Solid Aluminum",
        "B": "Slim Oblong Perforated Aluminum",
        "R": "Slim Round Perforated Aluminum",
        "Z": "Special Metal",
    },
    "Hardwood": _HARDWOOD_PANEL_TYPES,
    "Slim Hardwood": _HARDWOOD_PANEL_TYPES,
    "Vinyl": {
        "A": "Vinyl - Antique White",
        "B": "Vinyl - Birch",
        "C": "Vinyl - Cherry",
        "D": "Vinyl - Dark Oak",
        "F": "Vinyl - Champagne",
        "G": "Vinyl - Grey",
        "K": "Vinyl - Black",
        "L": "Vinyl - Light Oak",
        "M": "Vinyl - Mahogany",
        "N": "Vinyl - Walnut",
        "P": "Vinyl - Maple",
        "T": "Vinyl - Texture/Chalk",
        "W": "Vinyl - White",
        "S": "Special Vinyl",
        "Z": "Fire Resistant Oak (Vinyl)",
    },
    "Slim Vinyl": {
        "A": "Slim Vinyl - Antique White",
        "B": "Slim Vinyl - Birch",
        "C": "Slim Vinyl - Cherry",
        "D": "Slim Vinyl - Dark Oak",
        "F": "Slim Vinyl - Champagne",
        "G": "Slim Vinyl - Grey",
        "K": "Slim Vinyl - Black",
        "L": "Slim Vinyl - Light Oak",
        "M": "Slim Vinyl - Mahogany",
        "N": "Slim Vinyl - Walnut",
        "P": "Slim Vinyl - Maple",
        "T": "Slim Vinyl - Texture/Chalk",
        "W": "Slim Vinyl - White",
        "S": "Special Slim Vinyl",
        "Z": "Fire Resistant Oak (Slim Vinyl)",
    },
}

_HANDING_MAP = {
    "L": "LH Center Pin",
    "R": "RH Center Pin",
    "E": "Center Pin Double Lead Post",
    "F": "Offset Pin Double Lead Post",
    "N": "LH Offset Pin",
    "P": "RH Offset Pin",
}

# Panel quantity mapping is duplicated for code "1" in the original spec. To
# keep a deterministic outcome we map code "1" to 10 panels when an even count
# is required by handing (E/F) and 11 otherwise. Code "0" is treated as 10 when
# an explicit even/odd hint is not provided.
_PANEL_COUNT_MAP = {
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "0": 10,
    "1": 11,  # Will be coerced to 10 for even-hand configurations.
    "2": 12,
    "3": 13,
    "4": 14,
    "5": 15,
}

_VISION_PANEL_QTY = {"0": 0, "1": 1, "2": 2, "3": 3}

_VISION_PANEL_COLOR = {
    "0": "None",
    "C": "Clear",
    "S": "Bronze",
    "B": "Oblong Perforated",
    "R": "Round Perforated",
}

_CENTER_PIN_HARDWARE = {
    "B": "Bronze",
    "N": "Nickle",
    "G": "Gold",
    "K": "Black",
    "W": "White",
    "S": "Special",
}

_OFFSET_PIN_HARDWARE = {
    "1": "Bronze",
    "2": "Nickle",
    "3": "Gold",
    "4": "Black",
    "5": "White",
    "6": "Special",
    "7": "Gold/White",
    "8": "Gold/Bronze",
    "9": "Gold/Black",
    "C": "White/Nickel",
    "D": "White/Bronze",
    "E": "Black/Nickle",
    "X": "Beige LP & Hinge",
}

_FRACTION_MAP = {
    "0": 0,
    "A": 1 / 16,
    "B": 1 / 8,
    "C": 3 / 16,
    "D": 1 / 4,
    "E": 5 / 16,
    "F": 3 / 8,
    "G": 7,
    "H": 1 / 2,
    "J": 9 / 16,
    "K": 5 / 8,
    "L": 11 / 16,
    "M": 3 / 4,
    "N": 13 / 16,
    "P": 7 / 8,
    "Q": 15 / 16,
    "R": 5 / 6,
}

_ADDERS = {
    "A": "Gate Arm",
    "C": "Custom Pin Position",
    "CB": "Cambridge",
    "DJ": "Deep Jamb",
    "G": "Inclinator GTS Kit",
    "W": "White Barrels",
    "B": "Black Barrels",
    "DB": "Dark Brown Barrels",
    "I": "Add to Inclinator #",
}


@dataclass
class ParsedGatePart:
    material_code: str
    material: str
    panel_type_code: str
    panel_material_color: str
    handing_code: str
    handing: str
    panel_count: int
    vision_panel_qty: int
    vision_panel_color: str
    hardware_option: str
    door_height_inches: float
    door_height_display: str
    adders: List[str]


def _parse_material(part_number: str) -> tuple[str, str, int]:
    if len(part_number) < 2:
        raise GatePartNumberError("Part number too short to determine material.")

    prefix = part_number[:2]
    if prefix in _TWO_CHAR_MATERIALS:
        material_code = prefix
        consumed = 2
    else:
        material_code = part_number[0]
        consumed = 1

    if material_code not in _MATERIAL_MAP:
        raise GatePartNumberError(f"Unknown material code '{material_code}'.")

    return material_code, _MATERIAL_MAP[material_code], consumed


def _format_height(integer_inches: int, fraction_value: float) -> tuple[float, str]:
    total_height = integer_inches + fraction_value
    if fraction_value == 0:
        return total_height, f"{integer_inches}\""

    # Build a readable fraction string when possible
    fraction_map = {
        1 / 16: "1/16",
        1 / 8: "1/8",
        3 / 16: "3/16",
        1 / 4: "1/4",
        5 / 16: "5/16",
        3 / 8: "3/8",
        7: "7",  # As provided by the spec
        1 / 2: "1/2",
        9 / 16: "9/16",
        5 / 8: "5/8",
        11 / 16: "11/16",
        3 / 4: "3/4",
        13 / 16: "13/16",
        7 / 8: "7/8",
        15 / 16: "15/16",
        5 / 6: "5/6",
    }
    fraction_str = fraction_map.get(fraction_value, str(fraction_value))
    return total_height, f"{integer_inches} {fraction_str}\""


def _parse_adders(segment: str) -> List[str]:
    adders: List[str] = []
    idx = 0
    # Prefer two-character adders when they exist to avoid ambiguity.
    two_char_codes = [code for code in _ADDERS if len(code) == 2]
    single_char_codes = [code for code in _ADDERS if len(code) == 1]

    while idx < len(segment):
        matched = None
        if idx + 1 < len(segment):
            pair = segment[idx : idx + 2]
            if pair in two_char_codes:
                matched = pair
                idx += 2

        if matched is None:
            code = segment[idx]
            if code in single_char_codes:
                matched = code
                idx += 1
            else:
                raise GatePartNumberError(
                    f"Unrecognized adder code starting at '{segment[idx:]}'."
                )

        adders.append(_ADDERS[matched])

    return adders


def parse_gate_part_number(part_number: str) -> ParsedGatePart:
    """Parse a gate part number into structured attributes.

    Raises:
        GatePartNumberError: if the part number fails validation.
    """

    normalized = (part_number or "").strip().upper()
    if not normalized:
        raise GatePartNumberError("Item Number is required.")

    material_code, material, idx = _parse_material(normalized)

    if idx >= len(normalized):
        raise GatePartNumberError("Part number ended before panel type was defined.")

    panel_type_code = normalized[idx]
    idx += 1
    material_key = material
    if material_key not in _PANEL_TYPE_MAP or panel_type_code not in _PANEL_TYPE_MAP[material_key]:
        raise GatePartNumberError(
            f"Panel type code '{panel_type_code}' is not valid for material {material}."
        )
    panel_material_color = _PANEL_TYPE_MAP[material_key][panel_type_code]

    try:
        handing_code = normalized[idx]
    except IndexError as exc:
        raise GatePartNumberError("Missing handing code.") from exc
    idx += 1
    if handing_code not in _HANDING_MAP:
        raise GatePartNumberError(f"Unknown handing code '{handing_code}'.")
    handing = _HANDING_MAP[handing_code]

    try:
        panel_count_code = normalized[idx]
    except IndexError as exc:
        raise GatePartNumberError("Missing panel quantity code.") from exc
    idx += 1
    if panel_count_code not in _PANEL_COUNT_MAP:
        raise GatePartNumberError(f"Unknown panel quantity code '{panel_count_code}'.")
    panel_count = _PANEL_COUNT_MAP[panel_count_code]
    if handing_code in {"E", "F"} and panel_count % 2 != 0:
        # Favor even panel count interpretation when required by handing.
        if panel_count_code == "1":
            panel_count = 10
        elif panel_count_code == "9":
            panel_count = 8
        else:
            raise GatePartNumberError(
                "Double lead/offset handing requires an even number of panels."
            )

    try:
        vision_qty_code = normalized[idx]
    except IndexError as exc:
        raise GatePartNumberError("Missing vision panel quantity code.") from exc
    idx += 1
    if vision_qty_code not in _VISION_PANEL_QTY:
        raise GatePartNumberError(
            f"Unknown vision panel quantity code '{vision_qty_code}'."
        )
    vision_panel_qty = _VISION_PANEL_QTY[vision_qty_code]

    try:
        vision_color_code = normalized[idx]
    except IndexError as exc:
        raise GatePartNumberError("Missing vision panel color code.") from exc
    idx += 1
    if vision_color_code not in _VISION_PANEL_COLOR:
        raise GatePartNumberError(
            f"Unknown vision panel color code '{vision_color_code}'."
        )
    vision_panel_color = _VISION_PANEL_COLOR[vision_color_code]

    try:
        hardware_code = normalized[idx]
    except IndexError as exc:
        raise GatePartNumberError("Missing hardware option code.") from exc
    idx += 1

    is_center_pin = handing_code in {"L", "R", "E"}
    hardware_map = _CENTER_PIN_HARDWARE if is_center_pin else _OFFSET_PIN_HARDWARE
    if hardware_code not in hardware_map:
        raise GatePartNumberError(
            f"Unknown hardware code '{hardware_code}' for {'center' if is_center_pin else 'offset'} pin handing."
        )
    hardware_option = hardware_map[hardware_code]

    if idx + 2 >= len(normalized):
        raise GatePartNumberError("Missing door height information.")
    height_digits = normalized[idx : idx + 2]
    if not height_digits.isdigit():
        raise GatePartNumberError("Door height inches must be numeric.")
    integer_inches = int(height_digits)
    idx += 2

    fraction_code = normalized[idx]
    idx += 1
    if fraction_code not in _FRACTION_MAP:
        raise GatePartNumberError(f"Unknown height fraction code '{fraction_code}'.")
    fraction_value = _FRACTION_MAP[fraction_code]
    door_height_inches, door_height_display = _format_height(
        integer_inches, fraction_value
    )

    remaining = normalized[idx:]
    adders = _parse_adders(remaining) if remaining else []

    return ParsedGatePart(
        material_code=material_code,
        material=material,
        panel_type_code=panel_type_code,
        panel_material_color=panel_material_color,
        handing_code=handing_code,
        handing=handing,
        panel_count=panel_count,
        vision_panel_qty=vision_panel_qty,
        vision_panel_color=vision_panel_color,
        hardware_option=hardware_option,
        door_height_inches=door_height_inches,
        door_height_display=door_height_display,
        adders=adders,
    )

