"""ZPL label generation utilities for receiving inventory."""

LABEL_WIDTH = 812  # dots for 4" width at 203 DPI
LABEL_HEIGHT = 1218  # dots for 6" height at 203 DPI

# Label element positions (x, y) in dots
SKU_TEXT_ORIGIN = (50, 50)
DESC_TEXT_ORIGIN = (50, 110)
QTY_TEXT_ORIGIN = (50, 170)
BARCODE_ORIGIN = (50, 230)


def build_receiving_label(sku: str, description: str, qty: int) -> str:
    """Build ZPL for an inventory receiving label.

    Parameters
    ----------
    sku:
        Stock keeping unit identifier.
    description:
        Human readable description of the item.
    qty:
        Quantity received.

    Returns
    -------
    str
        ZPL string encoding the label.
    """
    lines = [
        "^XA",
        f"^PW{LABEL_WIDTH}",
        f"^LL{LABEL_HEIGHT}",
        f"^FO{SKU_TEXT_ORIGIN[0]},{SKU_TEXT_ORIGIN[1]}^A0,N,50^FD{sku}^FS",
        f"^FO{DESC_TEXT_ORIGIN[0]},{DESC_TEXT_ORIGIN[1]}^A0,N,30^FD{description}^FS",
        f"^FO{QTY_TEXT_ORIGIN[0]},{QTY_TEXT_ORIGIN[1]}^A0,N,30^FDQty: {qty}^FS",
        f"^FO{BARCODE_ORIGIN[0]},{BARCODE_ORIGIN[1]}^BCN,100,Y,N,N^FD{sku}^FS",
        "^XZ",
    ]
    return "\n".join(lines)
