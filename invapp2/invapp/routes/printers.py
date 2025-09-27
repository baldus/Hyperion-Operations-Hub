import re

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
from invapp.models import LabelProcessAssignment, LabelTemplate, Printer
from invapp.printing import labels as label_defs
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

    db_templates = {
        template.name: template
        for template in LabelTemplate.query.order_by(LabelTemplate.name.asc()).all()
    }
    assignment_map: dict[str, set[str]] = {}
    for assignment in LabelProcessAssignment.query.join(LabelTemplate).all():
        assignment_map.setdefault(assignment.template.name, set()).add(assignment.process)

    def _friendly_name(name: str, description: str | None = None) -> str:
        if description:
            summary = description.split(".")[0].strip()
            if summary:
                return summary
        cleaned = name
        if cleaned.endswith("LabelTemplate"):
            cleaned = cleaned[: -len("LabelTemplate")]
        return " ".join(filter(None, re.sub(r"(?<!^)(?=[A-Z])", " ", cleaned).split())) or name

    def _serialize_template(definition: label_defs.LabelDefinition) -> dict[str, object]:
        related = db_templates.get(definition.name)
        triggers: set[str] = set(definition.triggers)
        if related and related.trigger:
            triggers.add(related.trigger)
        triggers.update(assignment_map.get(definition.name, set()))

        layout = definition.layout or {}
        layout_elements = layout.get("elements") or []
        field_keys: set[str] = set(definition.fields.keys())
        for element in layout_elements:
            field_key = element.get("fieldKey") if isinstance(element, dict) else None
            if field_key:
                field_keys.add(str(field_key))

        return {
            "name": definition.name,
            "display_name": _friendly_name(definition.name, definition.description),
            "description": definition.description,
            "layout": layout,
            "fields": definition.fields,
            "field_keys": sorted(field_keys),
            "triggers": sorted(triggers),
            "source": "database" if related else "builtin",
        }

    template_names: set[str] = set(label_defs.LABEL_DEFINITIONS.keys())
    template_names.update(db_templates.keys())
    template_names.update(assignment_map.keys())

    available_templates = []
    for name in sorted(template_names):
        definition = label_defs.get_template_by_name(name)
        if definition is None:
            continue
        available_templates.append(_serialize_template(definition))
    default_template = next((entry["name"] for entry in available_templates), None)

    return render_template(
        "settings/label_designer.html",
        selected_printer=selected_printer,
        printers=printers,
        label_templates=available_templates,
        default_label_name=default_template,
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
