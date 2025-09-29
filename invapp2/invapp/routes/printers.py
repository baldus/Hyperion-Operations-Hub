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
from invapp.security import require_roles
from invapp.printing.labels import (
    build_designer_state,
    get_designer_label_config,
    get_designer_sample_context,
    iter_designer_labels,
    serialize_designer_layout,
)
from invapp.printing.zebra import print_label_for_process

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

    designer_labels: list[dict[str, object]] = []
    for config in iter_designer_labels():
        template = LabelTemplate.query.filter_by(name=config.template_name).first()
        if template is None:
            assignment = LabelProcessAssignment.query.filter_by(process=config.process).first()
            template = assignment.template if assignment is not None else None
        if template is not None:
            state = build_designer_state(
                config.id,
                template_layout=template.layout or {},
                template_fields=template.fields or {},
            )
        else:
            state = build_designer_state(config.id)
        designer_labels.append(state)

    return render_template(
        "settings/label_designer.html",
        selected_printer=selected_printer,
        printers=printers,
        designer_labels=designer_labels,
    )


@bp.post("/designer/print-trial")
@login_required
@require_roles("admin")
def label_designer_print_trial():
    payload = request.get_json(silent=True) or {}
    layout = payload.get("layout") if isinstance(payload.get("layout"), dict) else None
    label_id = payload.get("label_id") or (layout.get("id") if layout else None)
    if not label_id:
        return jsonify({"message": "Label identifier is required for a trial print."}), 400

    selected_printer = None
    selected_printer_id = session.get("selected_printer_id")
    if selected_printer_id:
        selected_printer = Printer.query.get(selected_printer_id)

    if selected_printer is None:
        return (
            jsonify({"message": "Select an active printer before sending a trial print."}),
            400,
        )

    config = get_designer_label_config(label_id)
    if config is None:
        return jsonify({"message": f"Unknown label '{label_id}'."}), 404

    template = LabelTemplate.query.filter_by(name=config.template_name).first()
    if template is None:
        return (
            jsonify({"message": "Save the label layout before printing a trial copy."}),
            400,
        )

    context = get_designer_sample_context(label_id)
    if not print_label_for_process(config.process, context):
        return (
            jsonify({"message": "Failed to queue the trial print with the active printer."}),
            500,
        )

    current_app.logger.info(
        "Label designer trial print queued for %s using template %s.",
        selected_printer.name,
        config.template_name,
    )

    return jsonify(
        {
            "ok": True,
            "message": f"Trial print queued for {selected_printer.name}.",
            "printer": selected_printer.name,
        }
    )

@bp.post("/designer/save")
@login_required
@require_roles("admin")
def label_designer_save_layout():
    payload = request.get_json(silent=True) or {}
    layout = payload.get("layout")
    if not isinstance(layout, dict):
        return (
            jsonify({"message": "Layout payload is required to save a label."}),
            400,
        )

    label_id = payload.get("label_id") or layout.get("id")
    if not label_id:
        return (
            jsonify({"message": "Label identifier is required to save a layout."}),
            400,
        )

    config = get_designer_label_config(label_id)
    if config is None:
        return jsonify({"message": f"Unknown label '{label_id}'."}), 404

    serialized = serialize_designer_layout(label_id, layout)

    template = LabelTemplate.query.filter_by(name=config.template_name).first()
    if template is None:
        template = LabelTemplate(name=config.template_name)
        db.session.add(template)

    template.description = config.description
    template.layout = serialized["layout"]
    template.fields = serialized["fields"]
    template.trigger = config.process

    assignment = LabelProcessAssignment.query.filter_by(process=config.process).first()
    if assignment is None:
        assignment = LabelProcessAssignment(process=config.process, template=template)
        db.session.add(assignment)
    else:
        assignment.template = template

    db.session.commit()

    current_app.logger.info(
        "Label designer layout saved for %s using template %s.",
        label_id,
        config.template_name,
    )

    return jsonify(
        {
            "ok": True,
            "message": f"Layout saved for {config.name}.",
            "label_id": label_id,
        }
    )
