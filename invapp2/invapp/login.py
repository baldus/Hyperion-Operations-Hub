"""Convenience re-exports for the Flask-Login API used by the app."""

from __future__ import annotations

from flask_login import (
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

