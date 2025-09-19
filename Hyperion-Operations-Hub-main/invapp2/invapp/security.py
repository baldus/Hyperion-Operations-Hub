"""Security helpers shared across the application."""
from functools import wraps

from flask import flash, redirect, request, session, url_for


def ensure_admin_access():
    """Ensure the current session has elevated admin privileges."""
    if session.get("is_admin"):
        return None

    next_target = request.full_path if request.query_string else request.path
    flash("Administrator access is required for that action.", "danger")
    return redirect(url_for("admin.login", next=next_target))


def admin_required(view_func):
    """Decorator that requires an active admin session for access."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        redirect_response = ensure_admin_access()
        if redirect_response is not None:
            return redirect_response
        return view_func(*args, **kwargs)

    return wrapped
