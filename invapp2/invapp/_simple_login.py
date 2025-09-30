"""Minimal fallback implementation of the :mod:`flask_login` API.

This module only implements the functionality that the application relies on
so that the project can still run in environments where the ``flask-login``
package is not installed.  It provides a ``LoginManager`` along with the
``current_user`` proxy, ``login_user``/``logout_user`` helpers, the
``login_required`` decorator, and a basic ``UserMixin``.

The goal is feature parity with the subset of behaviour used in the project,
not a complete reimplementation of Flask-Login.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Optional

from flask import abort, g, redirect, request, session, url_for
from werkzeug.local import LocalProxy


class AnonymousUser:
    """Simple stand-in for ``flask_login.AnonymousUserMixin``."""

    is_authenticated = False
    is_active = False
    is_anonymous = True

    def get_id(self) -> Optional[str]:  # pragma: no cover - mirrors flask-login
        return None


class UserMixin:
    """Lightweight replacement for :class:`flask_login.UserMixin`."""

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        identifier = getattr(self, "id", None)
        if identifier is None:
            raise AttributeError("User object must have an 'id' attribute")
        return str(identifier)


_login_manager: "LoginManager | None" = None


def _get_login_manager() -> "LoginManager":
    if _login_manager is None:  # pragma: no cover - defensive programming
        raise RuntimeError(
            "LoginManager has not been initialised. Instantiate it before "
            "using login-related helpers."
        )
    return _login_manager


def _get_current_user() -> Any:
    user = getattr(g, "_current_user", None)
    if user is None:
        user = AnonymousUser()
        g._current_user = user
    return user


current_user = LocalProxy(_get_current_user)


class LoginManager:
    """Very small subset of Flask-Login's ``LoginManager``."""

    def __init__(self) -> None:
        global _login_manager
        _login_manager = self
        self.login_view: Optional[str] = None
        self._user_callback: Optional[Callable[[str], Any]] = None
        self._unauthorized_callback: Optional[Callable[[], Any]] = None

    def init_app(self, app) -> None:  # type: ignore[override]
        @app.before_request
        def load_logged_in_user() -> None:
            user_id = session.get("_user_id")
            if user_id is None or self._user_callback is None:
                g._current_user = AnonymousUser()
                return

            user = self._user_callback(user_id)
            if user is None:
                session.pop("_user_id", None)
                g._current_user = AnonymousUser()
            else:
                g._current_user = user

        @app.context_processor
        def inject_current_user() -> dict[str, Any]:
            return {"current_user": current_user}

    def user_loader(self, callback: Callable[[str], Any]) -> Callable[[str], Any]:
        self._user_callback = callback
        return callback

    def unauthorized_handler(self, callback: Callable[[], Any]) -> Callable[[], Any]:
        self._unauthorized_callback = callback
        return callback

    def unauthorized(self):
        if self._unauthorized_callback is not None:
            return self._unauthorized_callback()
        if self.login_view:
            return redirect(url_for(self.login_view, next=request.url))
        abort(401)


def login_user(user: Any) -> None:
    session["_user_id"] = user.get_id()  # type: ignore[attr-defined]
    g._current_user = user


def logout_user() -> None:
    session.pop("_user_id", None)
    g._current_user = AnonymousUser()


def login_required(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:  # type: ignore[has-type]
            return _get_login_manager().unauthorized()
        return func(*args, **kwargs)

    return wrapper


__all__ = [
    "LoginManager",
    "UserMixin",
    "current_user",
    "login_required",
    "login_user",
    "logout_user",
]

