from functools import wraps
from typing import Sequence

from flask import abort

from invapp.login import current_user, login_required
from invapp.permissions import ensure_page_access, resolve_view_roles


def page_access_required(page_name: str, *, default_roles: Sequence[str] | None = None):
    """Decorator that enforces the configured roles for a given page."""

    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            allowed_roles = resolve_view_roles(page_name, default_roles=default_roles)
            if allowed_roles and current_user.has_any_role(allowed_roles):
                return f(*args, **kwargs)
            abort(403)

        return wrapped

    return decorator


def blueprint_page_guard(page_name: str, *, default_roles: Sequence[str] | None = None):
    """Return a ``before_request`` handler that enforces page access."""

    def handler():
        return ensure_page_access(page_name, default_roles=default_roles)

    return handler
