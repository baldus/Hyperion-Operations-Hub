from flask import Blueprint, render_template

bp = Blueprint("orders", __name__, url_prefix="/orders")

@bp.route("/")
def orders_home():
    return render_template("orders/home.html")
