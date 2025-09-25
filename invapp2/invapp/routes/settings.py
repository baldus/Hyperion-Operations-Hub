from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from invapp.auth import role_required
from invapp.extensions import db
from invapp.login import current_user
from invapp.models import Role, User

bp = Blueprint("settings", __name__, url_prefix="/settings")

@bp.route("/")
def settings_home():
    return render_template("settings/home.html")


@bp.route("/operations")
def operations_menu():
    return render_template("settings/operations_menu.html")

# --- Dark/Light Mode Toggle ---
@bp.route("/toggle-theme")
def toggle_theme():
    current = session.get("theme", "dark")
    session["theme"] = "light" if current == "dark" else "dark"
    return redirect(url_for("settings.settings_home"))


def _ensure_default_roles() -> None:
    defaults = {
        "user": "Standard application access",
        "admin": "Full administrative access",
    }
    created = False
    for name, description in defaults.items():
        if Role.query.filter_by(name=name).first():
            continue
        db.session.add(Role(name=name, description=description))
        created = True
    if created:
        db.session.commit()


def _is_last_admin(user: User) -> bool:
    admin_role = Role.query.filter_by(name="admin").first()
    if not admin_role:
        return False
    if admin_role not in user.roles:
        return False
    admin_count = sum(1 for admin in admin_role.users if admin.id != user.id)
    return admin_count == 0


@bp.route("/accounts", methods=["GET", "POST"])
@role_required("admin")
def manage_accounts():
    _ensure_default_roles()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        selected_roles = request.form.getlist("roles")

        if not username or not password:
            flash("Username and password are required.", "warning")
            return redirect(url_for("settings.manage_accounts"))

        if User.query.filter_by(username=username).first():
            flash("That username is already in use.", "warning")
            return redirect(url_for("settings.manage_accounts"))

        role_ids = {int(role_id) for role_id in selected_roles if role_id.isdigit()}
        roles = (
            Role.query.filter(Role.id.in_(role_ids)).all() if role_ids else []
        )

        if not roles:
            default_role = Role.query.filter_by(name="user").first()
            if default_role:
                roles = [default_role]

        new_user = User(username=username)
        new_user.set_password(password)
        new_user.roles = roles
        db.session.add(new_user)
        db.session.commit()
        flash(f"Created account for {username}.", "success")
        return redirect(url_for("settings.manage_accounts"))

    users = User.query.order_by(User.username.asc()).all()
    roles = Role.query.order_by(Role.name.asc()).all()
    return render_template("settings/accounts.html", users=users, roles=roles)


@bp.post("/accounts/<int:user_id>/update-roles")
@role_required("admin")
def update_user_roles(user_id: int):
    _ensure_default_roles()
    user = User.query.get_or_404(user_id)
    role_ids = {
        int(role_id) for role_id in request.form.getlist("roles") if role_id.isdigit()
    }
    roles = Role.query.filter(Role.id.in_(role_ids)).all() if role_ids else []

    admin_role = Role.query.filter_by(name="admin").first()
    if admin_role and admin_role in user.roles and admin_role not in roles:
        if _is_last_admin(user):
            flash("Cannot remove the last administrator.", "danger")
            return redirect(url_for("settings.manage_accounts"))

    if not roles:
        default_role = Role.query.filter_by(name="user").first()
        if default_role:
            roles = [default_role]

    user.roles = roles
    db.session.commit()
    flash(f"Updated roles for {user.username}.", "success")
    return redirect(url_for("settings.manage_accounts"))


@bp.post("/accounts/<int:user_id>/reset-password")
@role_required("admin")
def reset_user_password(user_id: int):
    _ensure_default_roles()
    user = User.query.get_or_404(user_id)
    new_password = request.form.get("new_password", "").strip()
    if not new_password:
        flash("New password is required.", "warning")
        return redirect(url_for("settings.manage_accounts"))

    user.set_password(new_password)
    db.session.commit()
    flash(f"Password reset for {user.username}.", "success")
    return redirect(url_for("settings.manage_accounts"))


@bp.post("/accounts/<int:user_id>/delete")
@role_required("admin")
def delete_user(user_id: int):
    _ensure_default_roles()
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own account.", "warning")
        return redirect(url_for("settings.manage_accounts"))

    if _is_last_admin(user):
        flash("Cannot delete the last administrator.", "danger")
        return redirect(url_for("settings.manage_accounts"))

    db.session.delete(user)
    db.session.commit()
    flash(f"Deleted account for {user.username}.", "info")
    return redirect(url_for("settings.manage_accounts"))
