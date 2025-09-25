from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from invapp.extensions import db
from invapp.login import current_user, login_required, login_user, logout_user
from invapp.models import User

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/register", methods=["GET", "POST"])
@login_required
def register():
    admin_username = current_app.config.get("ADMIN_USER", "superuser")
    if current_user.username != admin_username:
        abort(404)

    flash("User management has moved to the dedicated admin tools.", "info")
    return redirect(url_for("users.create"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            next_url = request.args.get("next")
            if next_url:
                return redirect(next_url)
            flash("Logged in", "success")
            return redirect(url_for("home"))
        flash("Invalid credentials", "danger")
    return render_template("auth/login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out", "success")
    return redirect(url_for("auth.login"))


@bp.route("/reset-password", methods=["GET", "POST"])
@login_required
def reset_password():
    if request.method == "POST":
        old = request.form["old_password"].strip()
        new = request.form["new_password"].strip()
        if current_user.check_password(old):
            current_user.set_password(new)
            db.session.commit()
            flash("Password updated", "success")
            return redirect(url_for("home"))
        flash("Invalid current password", "danger")
    return render_template("auth/reset_password.html")
