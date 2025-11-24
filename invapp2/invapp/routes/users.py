from __future__ import annotations

from typing import Iterable, Optional

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy.exc import IntegrityError

from invapp.extensions import db
from invapp.models import Role, User
from invapp.permissions import (
    get_known_pages,
    resolve_page_permissions,
    update_page_permissions,
)
from invapp.offline import is_emergency_mode_active
from invapp.superuser import superuser_required

bp = Blueprint("users", __name__, url_prefix="/users")


def _database_available() -> bool:
    return not is_emergency_mode_active()


def _offline_user_admin_response():
    recovery_steps = current_app.config.get("DATABASE_RECOVERY_STEPS", ())
    return render_template(
        "users/offline.html",
        recovery_steps=recovery_steps,
    )


@bp.route("/")
@superuser_required
def list_users():
    if not _database_available():
        return _offline_user_admin_response()

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
        viewer_role = Role.query.filter_by(name="viewer").first()
        if viewer_role is None:
            viewer_role = Role(name="viewer", description="Read-only user")
            db.session.add(viewer_role)

        legacy_role = Role.query.filter_by(name="user").first()
        if legacy_role is None:
            legacy_role = Role(name="user", description="Legacy standard user")
            db.session.add(legacy_role)

        return [viewer_role, legacy_role]
    return list(Role.query.filter(Role.id.in_(role_ids)).order_by(Role.name))


@bp.route("/create", methods=["GET", "POST"])
@superuser_required
def create():
    if not _database_available():
        return _offline_user_admin_response()

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

        try:
            User.commit_with_sequence_retry(user)
        except IntegrityError:
            current_app.logger.exception("Failed to create user")
            flash("Unable to create user due to a database error.", "danger")
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

@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@superuser_required
def edit(user_id: int):
    if not _database_available():
        return _offline_user_admin_response()

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
        user.roles = _selected_roles(role_ids)
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
    if not _database_available():
        return _offline_user_admin_response()

    roles = Role.query.order_by(Role.name).all()
    pages = get_known_pages()

    if request.method == "POST":
        error_message: Optional[str] = None
        for page in pages:
            page_name = page["page_name"]
            view_ids: list[int] = []
            edit_ids: list[int] = []
            for role in roles:
                field_name = f"page_permission-{page_name}-{role.id}"
                submitted_value = request.form.get(field_name, "hidden")
                if submitted_value not in {"hidden", "view", "edit"}:
                    error_message = "Invalid permission selection submitted."
                    break
                if role.name == "public" and submitted_value == "edit":
                    error_message = "Public access cannot be granted edit permissions."
                    break
                if submitted_value == "view":
                    view_ids.append(role.id)
                elif submitted_value == "edit":
                    edit_ids.append(role.id)
                    view_ids.append(role.id)
            if error_message:
                break
            update_page_permissions(
                page_name,
                view_ids,
                edit_ids,
                label=page.get("label"),
            )

        if error_message:
            return render_template(
                "users/page_permissions.html",
                roles=roles,
                pages=_build_page_entries(pages, roles),
                error_message=error_message,
            )

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
    entries = []
    for page in pages:
        page_name = page["page_name"]
        permissions = resolve_page_permissions(page_name)
        role_entries = []
        for role in roles:
            if role.name in permissions.edit_roles:
                level = "edit"
            elif role.name in permissions.view_roles:
                level = "view"
            else:
                level = "hidden"
            role_entries.append(
                {
                    "id": role.id,
                    "name": role.name,
                    "level": level,
                    "allow_edit": role.name != "public",
                }
            )
        entries.append(
            {
                "page_name": page_name,
                "label": page.get("label", permissions.label),
                "default_view_roles": tuple(
                    page.get("default_view_roles", permissions.view_roles)
                ),
                "default_edit_roles": tuple(
                    page.get("default_edit_roles", permissions.edit_roles)
                ),
                "role_entries": role_entries,
            }
        )
    entries.sort(key=lambda entry: entry["label"].lower())
    return entries


@bp.route("/<int:user_id>/reset-password", methods=["GET", "POST"])
@superuser_required
def reset_password(user_id: int):
    if not _database_available():
        return _offline_user_admin_response()

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
    if not _database_available():
        flash("User management requires the database to be online.", "warning")
        return redirect(url_for("admin.tools"))

    user = User.query.get_or_404(user_id)
    admin_username = current_app.config.get("ADMIN_USER", "superuser")

    if user.username == admin_username:
        flash("The superuser account cannot be deleted.", "warning")
        return redirect(url_for("users.list_users"))

    db.session.delete(user)
    db.session.commit()
    flash("User deleted.", "success")
    return redirect(url_for("users.list_users"))
