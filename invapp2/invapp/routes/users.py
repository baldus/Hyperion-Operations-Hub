from __future__ import annotations

from functools import wraps
from typing import Iterable

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from invapp.extensions import db
from invapp.login import current_user, login_required
from invapp.models import PageAccessRule, Role, User
from invapp.permissions import get_known_pages, resolve_allowed_roles, update_page_roles

bp = Blueprint("users", __name__, url_prefix="/users")


def _is_superuser() -> bool:
    if not current_user.is_authenticated:
        return False

    user_id = session.get("_user_id")
    if not user_id:
        return False

    try:
        user = User.query.get(int(user_id))
    except (TypeError, ValueError):
        return False

    if user is None:
        return False

    admin_username = current_app.config.get("ADMIN_USER", "superuser")
    return user.username == admin_username


def superuser_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_superuser():
            abort(403)
        return view(*args, **kwargs)

    return login_required(wrapped)


@bp.route("/")
@superuser_required
def list_users():
    users = User.query.order_by(User.username).all()
    return render_template("users/list.html", users=users)


def _extract_role_ids(raw_role_ids: Iterable[str]) -> list[int]:
    role_ids: list[int] = []
    for raw_id in raw_role_ids:
        try:
            role_ids.append(int(raw_id))
        except (TypeError, ValueError):
            flash("Invalid role selection.", "danger")
            return []
    return role_ids


def _selected_roles(role_ids: list[int]) -> list[Role]:
    if not role_ids:
        default_role = Role.query.filter_by(name="user").first()
        if default_role is None:
            default_role = Role(name="user", description="Standard user")
            db.session.add(default_role)
        return [default_role]
    return list(Role.query.filter(Role.id.in_(role_ids)).order_by(Role.name))


@bp.route("/create", methods=["GET", "POST"])
@superuser_required
def create():
    roles = Role.query.order_by(Role.name).all()
    selected_roles = set(request.form.getlist("roles")) if request.method == "POST" else set()
    username_value = request.form.get("username", "").strip()

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        if not username_value or not password:
            flash("Username and password are required.", "danger")
            return render_template(
                "users/form.html",
                roles=roles,
                selected_roles=selected_roles,
                username_value=username_value,
                form_action=url_for("users.create"),
                title="Create User",
                submit_label="Create",
                include_password=True,
            )

        if User.query.filter_by(username=username_value).first():
            flash("Username already exists.", "danger")
            return render_template(
                "users/form.html",
                roles=roles,
                selected_roles=selected_roles,
                username_value=username_value,
                form_action=url_for("users.create"),
                title="Create User",
                submit_label="Create",
                include_password=True,
            )

        role_ids = _extract_role_ids(request.form.getlist("roles"))
        if request.form.getlist("roles") and not role_ids:
            return render_template(
                "users/form.html",
                roles=roles,
                selected_roles=selected_roles,
                username_value=username_value,
                form_action=url_for("users.create"),
                title="Create User",
                submit_label="Create",
                include_password=True,
            )

        user = User(username=username_value)
        user.set_password(password)
        user.roles = _selected_roles(role_ids)
        db.session.add(user)
        db.session.commit()
        flash("User created.", "success")
        return redirect(url_for("users.list_users"))

    return render_template(
        "users/form.html",
        roles=roles,
        selected_roles=selected_roles,
        username_value=username_value,
        form_action=url_for("users.create"),
        title="Create User",
        submit_label="Create",
        include_password=True,
    )


def _page_role_ids(form, page_name: str) -> list[int]:
    field_name = f"page_roles-{page_name}"
    return _extract_role_ids(form.getlist(field_name))


def _selected_page_role_ids(page_name: str, default_roles: tuple[str, ...]) -> set[int]:
    rule = PageAccessRule.query.filter_by(page_name=page_name).first()
    if rule and rule.roles:
        return {role.id for role in rule.roles}

    if not default_roles:
        default_roles = tuple(resolve_allowed_roles(page_name))

    fallback_roles = {role_name for role_name in default_roles if role_name}
    if not fallback_roles:
        return set()

    return {
        role.id
        for role in Role.query.filter(Role.name.in_(fallback_roles)).all()
    }


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@superuser_required
def edit(user_id: int):
    user = User.query.get_or_404(user_id)
    admin_username = current_app.config.get("ADMIN_USER", "superuser")
    roles = Role.query.order_by(Role.name).all()
    selected_roles = (
        set(request.form.getlist("roles"))
        if request.method == "POST"
        else {str(role.id) for role in user.roles}
    )
    username_value = request.form.get("username", user.username).strip()

    if request.method == "POST":
        if not username_value:
            flash("Username is required.", "danger")
            return render_template(
                "users/form.html",
                roles=roles,
                selected_roles=selected_roles,
                username_value=username_value,
                form_action=url_for("users.edit", user_id=user.id),
                title="Edit User",
                submit_label="Save Changes",
                include_password=False,
            )

        if (
            username_value != user.username
            and User.query.filter_by(username=username_value).first()
        ):
            flash("Username already exists.", "danger")
            return render_template(
                "users/form.html",
                roles=roles,
                selected_roles=selected_roles,
                username_value=username_value,
                form_action=url_for("users.edit", user_id=user.id),
                title="Edit User",
                submit_label="Save Changes",
                include_password=False,
            )

        if user.username == admin_username and username_value != admin_username:
            flash("The superuser username cannot be changed here.", "warning")
            username_value = user.username
            selected_roles = {str(role.id) for role in user.roles}
            return render_template(
                "users/form.html",
                roles=roles,
                selected_roles=selected_roles,
                username_value=username_value,
                form_action=url_for("users.edit", user_id=user.id),
                title="Edit User",
                submit_label="Save Changes",
                include_password=False,
            )

        role_ids = _extract_role_ids(request.form.getlist("roles"))
        if request.form.getlist("roles") and not role_ids:
            return render_template(
                "users/form.html",
                roles=roles,
                selected_roles=selected_roles,
                username_value=username_value,
                form_action=url_for("users.edit", user_id=user.id),
                title="Edit User",
                submit_label="Save Changes",
                include_password=False,
            )

        user.username = username_value
        user.roles = list(Role.query.filter(Role.id.in_(role_ids)).order_by(Role.name)) if role_ids else []
        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("users.list_users"))

    return render_template(
        "users/form.html",
        roles=roles,
        selected_roles=selected_roles,
        username_value=username_value,
        form_action=url_for("users.edit", user_id=user.id),
        title="Edit User",
        submit_label="Save Changes",
        include_password=False,
    )


@bp.route("/page-permissions", methods=["GET", "POST"])
@superuser_required
def page_permissions():
    roles = Role.query.order_by(Role.name).all()
    pages = get_known_pages()

    if request.method == "POST":
        for page in pages:
            page_name = page["page_name"]
            role_ids = _page_role_ids(request.form, page_name)
            if request.form.getlist(f"page_roles-{page_name}") and not role_ids:
                return render_template(
                    "users/page_permissions.html",
                    roles=roles,
                    pages=_build_page_entries(pages, roles),
                    error_message="Invalid role selection submitted.",
                )
            update_page_roles(page_name, role_ids, label=page.get("label"))

        db.session.commit()
        flash("Page permissions updated.", "success")
        return redirect(url_for("users.page_permissions"))

    return render_template(
        "users/page_permissions.html",
        roles=roles,
        pages=_build_page_entries(pages, roles),
        error_message=None,
    )


def _build_page_entries(pages, roles):
    role_map = {role.id: role for role in roles}
    entries = []
    for page in pages:
        page_name = page["page_name"]
        default_roles = tuple(page.get("default_roles", ()))
        selected_ids = _selected_page_role_ids(page_name, default_roles)
        entries.append(
            {
                "page_name": page_name,
                "label": page.get("label", page_name.title()),
                "default_roles": default_roles,
                "selected_role_ids": selected_ids,
                "selected_roles": [role_map[rid].name for rid in selected_ids if rid in role_map],
            }
        )
    entries.sort(key=lambda entry: entry["label"].lower())
    return entries


@bp.route("/<int:user_id>/reset-password", methods=["GET", "POST"])
@superuser_required
def reset_password(user_id: int):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        if not password:
            flash("New password is required.", "danger")
            return render_template("users/reset_password.html", user=user)

        user.set_password(password)
        db.session.commit()
        flash("Password reset.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/reset_password.html", user=user)


@bp.route("/<int:user_id>/delete", methods=["POST"])
@superuser_required
def delete(user_id: int):
    user = User.query.get_or_404(user_id)
    admin_username = current_app.config.get("ADMIN_USER", "superuser")

    if user.username == admin_username:
        flash("The superuser account cannot be deleted.", "warning")
        return redirect(url_for("users.list_users"))

    db.session.delete(user)
    db.session.commit()
    flash("User deleted.", "success")
    return redirect(url_for("users.list_users"))
