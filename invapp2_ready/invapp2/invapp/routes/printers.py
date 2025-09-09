from flask import Blueprint, render_template

bp = Blueprint("printers", __name__, url_prefix="/printers")

@bp.route("/")
def list_items():
    return render_template("printers/list.html")
