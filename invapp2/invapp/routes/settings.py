import os

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from werkzeug.utils import secure_filename

from invapp.auth import page_access_required
from invapp.security import require_admin_or_superuser
from invapp.services.floorplan import floorplan_dir, floorplan_exists, floorplan_path

bp = Blueprint("settings", __name__, url_prefix="/settings")

@bp.route("/")
@page_access_required("settings")
def settings_home():
    return render_template("settings/home.html")


# --- Dark/Light Mode Toggle ---
@bp.route("/toggle-theme", methods=["POST", "GET"])
def toggle_theme():
    current = session.get("theme", "dark")
    session["theme"] = "light" if current == "dark" else "dark"

    next_target = request.form.get("next") or request.args.get("next") or request.referrer
    if not next_target:
        next_target = url_for("settings.settings_home")
    return redirect(next_target)


@bp.route("/floorplan", methods=["GET", "POST"])
@require_admin_or_superuser
def floorplan_settings():
    if request.method == "POST":
        action = (request.form.get("action") or "upload").strip().lower()
        target_path = floorplan_path()

        if action == "delete":
            if os.path.exists(target_path):
                os.remove(target_path)
                flash("Inventory floorplan removed.", "success")
            else:
                flash("No inventory floorplan found to remove.", "info")
            return redirect(url_for("settings.floorplan_settings"))

        upload = request.files.get("floorplan_pdf")
        if upload is None or not upload.filename:
            flash("Select a PDF file to upload.", "danger")
            return redirect(url_for("settings.floorplan_settings"))

        filename = secure_filename(upload.filename)
        if not filename.lower().endswith(".pdf"):
            flash("Only PDF uploads are allowed.", "danger")
            return redirect(url_for("settings.floorplan_settings"))

        if upload.mimetype and not upload.mimetype.startswith("application/pdf"):
            flash("Uploaded file must be a PDF.", "danger")
            return redirect(url_for("settings.floorplan_settings"))

        os.makedirs(floorplan_dir(), exist_ok=True)
        upload.save(target_path)
        flash("Inventory floorplan uploaded.", "success")
        return redirect(url_for("settings.floorplan_settings"))

    return render_template("settings/floorplan_settings.html", floorplan_exists=floorplan_exists())
