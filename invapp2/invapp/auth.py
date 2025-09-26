from functools import wraps
from typing import Sequence

from flask import abort

from invapp.login import current_user, login_required
from invapp.permissions import ensure_page_access, resolve_allowed_roles


def role_required(role_name):
    """Decorator maintained for backwards compatibility."""

    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.has_role(role_name):
                abort(403)
            return f(*args, **kwargs)

        return wrapped

    return decorator


def page_access_required(page_name: str, *, default_roles: Sequence[str] | None = None):
    """Decorator that enforces the configured roles for a given page."""

    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            allowed_roles = resolve_allowed_roles(page_name, default_roles=default_roles)
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
