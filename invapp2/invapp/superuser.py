"""Superuser helpers shared across routes and templates."""

from __future__ import annotations

from functools import wraps

from flask import abort, current_app, session

from invapp.login import current_user, login_required
from invapp.models import User


def is_superuser() -> bool:
    """Return True when the active user matches the configured superuser."""

    if getattr(current_user, "is_emergency_user", False):
        return True

    if not current_user.is_authenticated:
        return False

    try:
        user_id = int(session.get("_user_id"))
    except (TypeError, ValueError):
        return False

    user = User.query.get(user_id)
    if user is None:
        return False

    admin_username = current_app.config.get("ADMIN_USER", "superuser")
    return user.username == admin_username


def superuser_required(view_func):
    """Restrict access to the configured superuser account."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_superuser():
            abort(403)
        return view_func(*args, **kwargs)

    return login_required(wrapped)

