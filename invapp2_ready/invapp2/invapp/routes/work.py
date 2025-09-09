from flask import Blueprint, render_template

bp = Blueprint("work", __name__, url_prefix="/work")

@bp.route("/")
def work_home():
    return render_template("work/home.html")
