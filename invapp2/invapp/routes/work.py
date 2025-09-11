from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("work", __name__, url_prefix="/work")


@bp.before_request
@login_required
def require_login():
    pass

@bp.route("/")
def work_home():
    return render_template("work/home.html")
