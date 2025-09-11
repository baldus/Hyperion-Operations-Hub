import socket


def generate_batch_label(batch):
    return (
        "^XA\n"
        "^CF0,40\n"
        f"^FO50,50^FD{batch.item.name}^FS\n"
        f"^FO50,100^FDSKU: {batch.item.sku}^FS\n"
        f"^FO50,150^FDLot: {batch.lot_number}^FS\n"
        f"^FO50,200^FDQty: {batch.quantity}^FS\n"
        "^BY2,3,60\n"
        "^FO50,260^BCN,100,Y,N,N\n"
        f"^FD{batch.id}^FS\n"
        "^XZ"
    )


def send_zpl(zpl_string, printer):
    with socket.create_connection((printer.connection, 9100), timeout=10) as sock:
        sock.sendall(zpl_string.encode("utf-8"))
