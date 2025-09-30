"""Shared security helpers and decorators for view protection."""

from __future__ import annotations

from functools import wraps
from typing import Iterable, Tuple

from flask import abort

from invapp.extensions import login_manager
from invapp.login import current_user


def _normalize_roles(role_names: Iterable[str]) -> Tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for name in role_names:
        if not name:
            continue
        if name in seen:
            continue
        unique.append(name)
        seen.add(name)
    return tuple(unique)


def require_roles(*role_names: str):
    """Decorator ensuring the active user has any of the provided roles."""

    normalized_roles = _normalize_roles(role_names)

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()

            if normalized_roles and not current_user.has_any_role(normalized_roles):
                abort(403)

            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def require_admin(view_func):
    """Decorator specialized for the administrator role."""

    return require_roles("admin")(view_func)

