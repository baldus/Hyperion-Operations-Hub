from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.exc import IntegrityError

from invapp.auth import blueprint_page_guard
from invapp.extensions import db
from invapp.login import current_user, login_required
from invapp.models import Printer
from invapp.security import require_roles

bp = Blueprint("printers", __name__, url_prefix="/settings/printers")

bp.before_request(blueprint_page_guard("printers"))


def _apply_printer_configuration(printer: Printer) -> None:
    current_app.config["ZEBRA_PRINTER_HOST"] = printer.host
    if printer.port is not None:
        current_app.config["ZEBRA_PRINTER_PORT"] = printer.port


@bp.route("/", methods=["GET", "POST"], strict_slashes=False)
@login_required
@require_roles("admin")
def printer_settings():
    printers = Printer.query.order_by(Printer.name.asc()).all()
    selected_printer = None
    selected_printer_id = session.get("selected_printer_id")
    if selected_printer_id:
        selected_printer = Printer.query.get(selected_printer_id)

    if request.method == "POST":
        form_id = request.form.get("form_id")
        if form_id == "select":
            printer_id = request.form.get("printer_id")
            if not printer_id:
                flash("Please choose a printer to use.", "warning")
            else:
                try:
                    printer = Printer.query.get(int(printer_id))
                except (TypeError, ValueError):
                    printer = None
                if printer is None:
                    flash("The selected printer could not be found.", "danger")
                else:
                    session["selected_printer_id"] = printer.id
                    _apply_printer_configuration(printer)
                    flash(f"Using {printer.name} for printing tasks.", "success")
            return redirect(url_for("printers.printer_settings"))

        if form_id == "add":
            name = request.form.get("name", "").strip()
            printer_type = request.form.get("printer_type", "").strip()
            location = request.form.get("location", "").strip()
            host = request.form.get("host", "").strip()
            port_raw = request.form.get("port", "").strip()
            notes = request.form.get("notes", "").strip()

            errors: list[str] = []
            if not name:
                errors.append("Printer name is required.")
            if not host:
                errors.append("Connection host or IP is required.")

            port: int | None = None
            if port_raw:
                try:
                    port = int(port_raw)
                    if port <= 0:
                        raise ValueError
                except ValueError:
                    errors.append("Port must be a positive number.")

            if errors:
                for error in errors:
                    flash(error, "danger")
            else:
                printer = Printer(
                    name=name,
                    printer_type=printer_type or None,
                    location=location or None,
                    host=host,
                    port=port,
                    notes=notes or None,
                )
                db.session.add(printer)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    flash("A printer with that name already exists.", "danger")
                else:
                    flash(f"Added printer '{printer.name}'.", "success")
                    if (
                        request.form.get("make_default") == "yes"
                        or session.get("selected_printer_id") is None
                    ):
                        session["selected_printer_id"] = printer.id
                        _apply_printer_configuration(printer)
                        flash(f"'{printer.name}' is now the active printer.", "info")
                return redirect(url_for("printers.printer_settings"))

    if selected_printer is None and selected_printer_id:
        session.pop("selected_printer_id", None)

    if selected_printer is None and printers:
        configured_host = current_app.config.get("ZEBRA_PRINTER_HOST")
        configured_port = current_app.config.get("ZEBRA_PRINTER_PORT")
        if configured_host:
            selected_printer = (
                Printer.query.filter_by(host=configured_host, port=configured_port)
                .order_by(Printer.updated_at.desc())
                .first()
            )
        if selected_printer is None:
            selected_printer = printers[0]

    if selected_printer:
        session.setdefault("selected_printer_id", selected_printer.id)
        _apply_printer_configuration(selected_printer)

    return render_template(
        "settings/printer_settings.html",
        printers=printers,
        selected_printer=selected_printer,
        zebra_host=current_app.config.get("ZEBRA_PRINTER_HOST", ""),
        zebra_port=current_app.config.get("ZEBRA_PRINTER_PORT", ""),
        is_admin=current_user.has_role("admin"),
    )


@bp.route("/designer", methods=["GET"], strict_slashes=False)
@login_required
@require_roles("admin")
def label_designer():
    printers = Printer.query.order_by(Printer.name.asc()).all()
    selected_printer = None
    selected_printer_id = session.get("selected_printer_id")
    if selected_printer_id:
        selected_printer = Printer.query.get(selected_printer_id)

    if selected_printer is None and printers:
        selected_printer = printers[0]
        session.setdefault("selected_printer_id", selected_printer.id)

    if selected_printer:
        _apply_printer_configuration(selected_printer)

    return render_template(
        "settings/label_designer.html",
        selected_printer=selected_printer,
        printers=printers,
    )


@bp.post("/designer/print-trial")
@login_required
@require_roles("admin")
def label_designer_print_trial():
    payload = request.get_json(silent=True) or {}
    layout = payload.get("layout")
    if not isinstance(layout, dict):
        return jsonify({"message": "Layout payload is required for a trial print."}), 400

    selected_printer = None
    selected_printer_id = session.get("selected_printer_id")
    if selected_printer_id:
        selected_printer = Printer.query.get(selected_printer_id)

    if selected_printer is None:
        return (
            jsonify({"message": "Select an active printer before sending a trial print."}),
            400,
        )

    current_app.logger.info(
        "Label designer trial print queued for %s: %s", selected_printer.name, layout
    )

    return jsonify(
        {
            "ok": True,
            "message": f"Trial print queued for {selected_printer.name}.",
            "printer": selected_printer.name,
        }
    )
