from flask import Blueprint, render_template, request, redirect, url_for, flash
from invapp.extensions import db
from invapp.models import Printer
from invapp.printing.zebra import send_zpl

bp = Blueprint("printers", __name__, url_prefix="/printers")


@bp.route("/", methods=["GET"])
def list_printers():
    printers = Printer.query.all()
    return render_template("settings/printers.html", printers=printers)


@bp.route("/", methods=["POST"])
def save_printer():
    name = request.form.get("name", "").strip()
    connection = request.form.get("connection", "").strip()
    label_width = request.form.get("label_width", type=float)
    label_height = request.form.get("label_height", type=float)

    if not name or not connection:
        flash("Name and connection are required", "danger")
        return redirect(url_for("printers.list_printers"))

    printer = Printer(name=name, connection=connection,
                      label_width=label_width, label_height=label_height)
    db.session.add(printer)
    db.session.commit()
    flash("Printer saved", "success")
    return redirect(url_for("printers.list_printers"))


@bp.post("/<int:printer_id>/test")
def test_printer(printer_id):
    printer = Printer.query.get_or_404(printer_id)
    sample_zpl = "^XA^FO50,50^FDTest Print^FS^XZ"
    try:
        send_zpl(sample_zpl, printer)
        flash("Test label sent", "success")
    except Exception as exc:
        flash(f"Test print failed: {exc}", "danger")
    return redirect(url_for("printers.list_printers"))
