"""Helpers for providing limited access when the database is offline."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Iterable, Tuple

from flask import current_app
from flask_login import AnonymousUserMixin


@dataclass(frozen=True)
class _OfflineRole:
    """Minimal role representation for emergency access."""

    name: str
    description: str = ""


class OfflineAdminUser(AnonymousUserMixin):
    """Acts as an administrator when the database is unavailable.

    The application falls back to this anonymous user implementation whenever
    the configured database cannot be reached during startup.  By reporting as
    an authenticated administrator, the UI and admin tools remain accessible so
    operators can troubleshoot the outage.
    """

    #: Roles granted while the database is offline.  These mirror the default
    #: application roles so navigation and permission checks continue to work.
    _ROLE_NAMES: Tuple[str, ...] = (
        "admin",
        "editor",
        "viewer",
        "user",
        "orders",
        "inventory",
        "production",
        "purchasing",
        "quality",
        "reports",
        "public",
    )

    username = "offline-admin"
    display_name = "Offline Administrator"

    def _emergency_mode_active(self) -> bool:
        try:
            return not current_app.config.get("DATABASE_AVAILABLE", True)
        except RuntimeError:  # pragma: no cover - outside an application context
            return False

    @property
    def is_authenticated(self) -> bool:  # type: ignore[override]
        return self._emergency_mode_active()

    @property
    def is_active(self) -> bool:  # type: ignore[override]
        return self._emergency_mode_active()

    @property
    def is_anonymous(self) -> bool:  # type: ignore[override]
        return not self._emergency_mode_active()

    @cached_property
    def _role_objects(self) -> Tuple[_OfflineRole, ...]:
        return tuple(_OfflineRole(name) for name in self._ROLE_NAMES)

    @property
    def roles(self) -> Tuple[_OfflineRole, ...]:  # type: ignore[override]
        if not self._emergency_mode_active():
            return tuple()
        return self._role_objects

    def has_role(self, role_name: str) -> bool:  # pragma: no cover - thin wrapper
        if not role_name:
            return False
        if not self._emergency_mode_active():
            return role_name == "public"
        return True

    def has_any_role(self, role_names: Iterable[str]) -> bool:
        normalized = tuple(name for name in role_names if name)
        if not normalized:
            return False
        if not self._emergency_mode_active():
            return any(name == "public" for name in normalized)
        return True

    @property
    def id(self):  # type: ignore[override]
        return None

    def get_id(self) -> str | None:  # type: ignore[override]
        return None

    @property
    def is_emergency_user(self) -> bool:
        """Expose whether the implicit emergency access is active."""

        return self._emergency_mode_active()

    # -- Compatibility helpers -------------------------------------------------

    def check_password(self, _password: str) -> bool:  # pragma: no cover - defensive
        """Mirror the user interface without ever treating a password as valid."""

        return False

    def set_password(self, _password: str) -> None:  # pragma: no cover - defensive
        """Deny password mutations for the synthetic emergency principal."""

        raise RuntimeError("Emergency access sessions cannot change passwords.")


def is_emergency_mode_active() -> bool:
    """Return ``True`` when the console is running without a database."""

    try:
        return not current_app.config.get("DATABASE_AVAILABLE", True)
    except RuntimeError:  # pragma: no cover - outside application context
        return False
