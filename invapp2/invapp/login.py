"""Compatibility layer for login utilities.

This module prefers the real :mod:`flask_login` implementation when it is
available but falls back to a lightweight in-repo substitute that exposes the
subset of functionality the application uses.  Downstream code can import from
here without worrying about whether the external dependency has been installed.
"""

from __future__ import annotations

try:  # pragma: no cover - exercised when flask-login is installed
    from flask_login import (  # type: ignore[import-not-found]
        LoginManager,
        UserMixin,
        current_user,
        login_required,
        login_user,
        logout_user,
    )
except ModuleNotFoundError:  # pragma: no cover - covered by fallback tests
    from ._simple_login import (  # noqa: F401
        LoginManager,
        UserMixin,
        current_user,
        login_required,
        login_user,
        logout_user,
    )

__all__ = [
    "LoginManager",
    "UserMixin",
    "current_user",
    "login_required",
    "login_user",
    "logout_user",
]

