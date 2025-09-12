from flask import Blueprint, render_template, request, session

from invapp.auth import role_required

bp = Blueprint("printers", __name__, url_prefix="/settings")


@bp.route("/printers", methods=["GET", "POST"])
@role_required("admin")
def printers_home():
    if request.method == "POST":
        theme = request.form.get("theme")
        if theme:
            session["theme"] = theme
    theme = session.get("theme", "dark")
    return render_template("settings/printers.html", theme=theme, is_admin=True)
