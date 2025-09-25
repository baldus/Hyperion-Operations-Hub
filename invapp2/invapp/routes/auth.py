from flask import Blueprint, flash, redirect, render_template, request, url_for
from invapp.extensions import db
from invapp.login import current_user, login_required, login_user, logout_user
from invapp.models import Role, User

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        if not username or not password:
            flash("Username and password required", "danger")
            return redirect(url_for("auth.register"))
        if User.query.filter_by(username=username).first():
            flash("Username already exists", "danger")
            return redirect(url_for("auth.register"))
        user = User(username=username)
        user.set_password(password)
        role = Role.query.filter_by(name="user").first()
        if not role:
            role = Role(name="user")
            db.session.add(role)
        user.roles.append(role)
        db.session.add(user)
        db.session.commit()
        flash("Registration successful", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html")


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
