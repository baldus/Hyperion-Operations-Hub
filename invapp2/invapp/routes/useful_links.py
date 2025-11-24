from __future__ import annotations

from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for

from invapp.extensions import db
from invapp.models import UsefulLink
from invapp.permissions import ensure_page_access, resolve_edit_roles
from invapp.permissions import principal_has_any_role
from invapp.superuser import is_superuser

LINK_EDIT_DEFAULT_ROLES: tuple[str, ...] = ("admin",)


def _links_editor_guard():
    if is_superuser():
        return None

    return ensure_page_access(
        "useful_links",
        default_roles=LINK_EDIT_DEFAULT_ROLES,
        default_edit_roles=LINK_EDIT_DEFAULT_ROLES,
    )


def can_manage_useful_links() -> bool:
    if is_superuser():
        return True

    edit_roles = resolve_edit_roles("useful_links", default_roles=LINK_EDIT_DEFAULT_ROLES)
    return principal_has_any_role(edit_roles, require_auth=True)


def links_editor_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        guard_response = _links_editor_guard()
        if guard_response is not None:
            return guard_response
        return view_func(*args, **kwargs)

    return wrapped

bp = Blueprint("useful_links", __name__, url_prefix="/links")


def _parse_display_order(raw_value: str) -> tuple[int | None, list[str]]:
    errors: list[str] = []
    if not raw_value:
        return 0, errors

    try:
        value = int(raw_value)
        return value, errors
    except (TypeError, ValueError):
        errors.append("Display order must be a whole number.")
        return None, errors


def _validate_payload(form) -> tuple[str, str, str | None, int | None, list[str]]:
    title = (form.get("title") or "").strip()
    url = (form.get("url") or "").strip()
    description = (form.get("description") or "").strip()
    display_order_raw = (form.get("display_order") or "").strip()

    errors: list[str] = []
    if not title:
        errors.append("A title is required.")
    if not url:
        errors.append("A destination URL is required.")
    elif not url.startswith(("http://", "https://")):
        errors.append("Links must start with http:// or https://.")

    display_order, order_errors = _parse_display_order(display_order_raw)
    errors.extend(order_errors)

    return title, url, description or None, display_order, errors


@bp.route("/", methods=["GET", "POST"], strict_slashes=False)
@links_editor_required
def manage_links():
    links = UsefulLink.query.order_by(
        UsefulLink.display_order.asc(), UsefulLink.title.asc()
    ).all()

    if request.method == "POST":
        title, url, description, display_order, errors = _validate_payload(request.form)
        if errors:
            for error in errors:
                flash(error, "danger")
        else:
            link = UsefulLink(
                title=title,
                url=url,
                description=description,
                display_order=display_order or 0,
            )
            db.session.add(link)
            db.session.commit()
            flash("Link added", "success")
            return redirect(url_for("useful_links.manage_links"))

    return render_template("links/manage.html", links=links)


@bp.post("/<int:link_id>/update")
@links_editor_required
def update_link(link_id: int):
    link = UsefulLink.query.get_or_404(link_id)
    title, url, description, display_order, errors = _validate_payload(request.form)

    if errors:
        for error in errors:
            flash(error, "danger")
        return redirect(url_for("useful_links.manage_links"))

    link.title = title
    link.url = url
    link.description = description
    link.display_order = display_order or 0
    db.session.commit()
    flash("Link updated", "success")
    return redirect(url_for("useful_links.manage_links"))


@bp.post("/<int:link_id>/delete")
@links_editor_required
def delete_link(link_id: int):
    link = UsefulLink.query.get_or_404(link_id)
    db.session.delete(link)
    db.session.commit()
    flash("Link removed", "success")
    return redirect(url_for("useful_links.manage_links"))

