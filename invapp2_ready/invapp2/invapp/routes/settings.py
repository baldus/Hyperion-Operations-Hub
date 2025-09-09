from flask import Blueprint, render_template

bp = Blueprint("settings", __name__, url_prefix="/settings")

@bp.route("/")
def settings_home():
    return render_template("settings/home.html")

@bp.route("/printers")
def printers_home():
    return render_template("settings/printers.html")
