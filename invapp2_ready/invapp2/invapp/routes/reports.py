from flask import Blueprint, render_template

bp = Blueprint("reports", __name__, url_prefix="/reports")

@bp.route("/")
def list_reports():
    return render_template("reports/list.html")
