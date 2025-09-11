from flask import Blueprint, render_template, request, redirect, url_for, flash
from invapp.extensions import db
from invapp.models import Printer
import socket


bp = Blueprint("printers", __name__, url_prefix="/printers")


@bp.route("/", methods=["GET", "POST"])
def manage():
    """List printers and handle add/update submissions."""
    if request.method == "POST":
        form = request.form
        printer_id = form.get("id")
        label_width = form.get("label_width")
        label_height = form.get("label_height")
        if printer_id:
            printer = Printer.query.get(printer_id)
            if printer:
                printer.name = form.get("name")
                printer.connection = form.get("connection")
                printer.label_width = float(label_width) if label_width else None
                printer.label_height = float(label_height) if label_height else None
        else:
            printer = Printer(
                name=form.get("name"),
                connection=form.get("connection"),
                label_width=float(label_width) if label_width else None,
                label_height=float(label_height) if label_height else None,
            )
            db.session.add(printer)
        db.session.commit()
        flash("Printer saved", "success")
        return redirect(url_for("settings.printers.manage"))

    printers = Printer.query.all()
    return render_template("settings/printers.html", printers=printers)


@bp.route("/<int:printer_id>/test", methods=["POST"])
def test_printer(printer_id: int):
    """Send a sample ZPL job to the specified printer."""
    printer = Printer.query.get_or_404(printer_id)
    zpl = "^XA^FO50,50^ADN,36,20^FDTest Print^FS^XZ"
    try:
        if printer.connection.startswith("/"):
            with open(printer.connection, "wb") as handle:
                handle.write(zpl.encode("utf-8"))
        else:
            with socket.create_connection((printer.connection, 9100), timeout=5) as sock:
                sock.sendall(zpl.encode("utf-8"))
        flash("Test label sent", "success")
    except Exception as exc:  # pragma: no cover - network failure handling
        flash(f"Failed to send test label: {exc}", "error")
    return redirect(url_for("settings.printers.manage"))

