"""Helpers for managing per-page access control settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Mapping, Sequence

from flask import abort, current_app, request


from invapp.extensions import login_manager
from invapp.login import current_user
from invapp.models import PageAccessRule, Role, User, db

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class PagePermissionSpec:
    page_name: str
    label: str
    view_roles: tuple[str, ...]
    edit_roles: tuple[str, ...]


def _normalize_roles(role_names: Iterable[str]) -> tuple[str, ...]:
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


DEFAULT_PAGE_ACCESS: Mapping[str, Mapping[str, Sequence[str] | str]] = {
    "home": {
        "label": "Operations Dashboard",
        "view_roles": (
            "public",
            "viewer",
            "editor",
            "admin",
            "user",
            "orders",
            "inventory",
            "production",
            "reports",
        ),
        "edit_roles": ("admin",),
    },
    "inventory": {
        "label": "Inventory Dashboard",
        "view_roles": ("viewer", "editor", "admin", "inventory", "user"),
        "edit_roles": ("editor", "admin", "inventory"),
    },
    "orders": {
        "label": "Orders Workspace",
        "view_roles": ("viewer", "editor", "admin", "orders", "user"),
        "edit_roles": ("editor", "admin", "orders"),
    },
    "purchasing": {
        "label": "Item Shortages",
        "view_roles": ("viewer", "editor", "admin", "purchasing", "orders", "inventory"),
        "edit_roles": ("editor", "admin", "purchasing"),
    },
    "quality": {
        "label": "Quality & RMAs",
        "view_roles": ("admin", "quality"),
        "edit_roles": ("admin", "quality"),
    },
    "production": {
        "label": "Production History",
        "view_roles": ("viewer", "editor", "admin", "production", "user"),
        "edit_roles": ("editor", "admin", "production"),
    },
    "reports": {
        "label": "Reports",
        "view_roles": ("viewer", "editor", "admin", "reports", "user"),
        "edit_roles": ("admin", "reports"),
    },
    "work": {
        "label": "Workstations",
        "view_roles": ("public", "viewer", "editor", "admin", "production", "user"),
        "edit_roles": ("editor", "admin", "production"),
    },
    "settings": {
        "label": "Operations Settings",
        "view_roles": ("admin",),
        "edit_roles": ("admin",),
    },
    "printers": {
        "label": "Printer Tools",
        "view_roles": ("admin",),
        "edit_roles": ("admin",),
    },
    "admin": {
        "label": "Administration",
        "view_roles": ("admin",),
        "edit_roles": ("admin",),
    },
    "users": {
        "label": "User Management",
        "view_roles": ("admin",),
        "edit_roles": ("admin",),
    },
}


def _database_available() -> bool:
    """Return True when the configured database can be queried."""

    try:
        return current_app.config.get("DATABASE_AVAILABLE", True)
    except RuntimeError:  # pragma: no cover - outside an application context
        return True


def _default_permissions_for(page_name: str) -> PagePermissionSpec:
    config = DEFAULT_PAGE_ACCESS.get(page_name, {})
    label = config.get("label", page_name.replace("_", " ").title())
    view_roles = _normalize_roles(config.get("view_roles", ("admin",)))
    edit_roles = _normalize_roles(config.get("edit_roles", ("admin",)))
    if not edit_roles:
        edit_roles = ("admin",)
    if not view_roles:
        view_roles = ("admin",)
    return PagePermissionSpec(page_name=page_name, label=label, view_roles=view_roles, edit_roles=edit_roles)


def lookup_page_label(page_name: str) -> str:
    if not _database_available():
        return _default_permissions_for(page_name).label
    rule = PageAccessRule.query.filter_by(page_name=page_name).first()
    if rule and rule.label:
        return rule.label
    return _default_permissions_for(page_name).label


def resolve_page_permissions(
    page_name: str,
    *,
    default_view_roles: Sequence[str] | None = None,
    default_edit_roles: Sequence[str] | None = None,
) -> PagePermissionSpec:
    default_spec = _default_permissions_for(page_name)
    if not _database_available():
        view_roles = _normalize_roles(default_view_roles or default_spec.view_roles)
        edit_roles = _normalize_roles(default_edit_roles or default_spec.edit_roles)
        return PagePermissionSpec(
            page_name=page_name,
            label=default_spec.label,
            view_roles=view_roles,
            edit_roles=edit_roles,
        )

    rule = PageAccessRule.query.filter_by(page_name=page_name).first()

    if rule is None:
        view_roles = _normalize_roles(default_view_roles or default_spec.view_roles)
        edit_roles = _normalize_roles(default_edit_roles or default_spec.edit_roles)
        return PagePermissionSpec(
            page_name=page_name,
            label=default_spec.label,
            view_roles=view_roles,
            edit_roles=edit_roles,
        )

    label = rule.label or default_spec.label
    view_roles = _normalize_roles(role.name for role in rule.view_roles)
    edit_roles = _normalize_roles(role.name for role in rule.edit_roles)

    if not view_roles:
        view_roles = _normalize_roles(default_view_roles or default_spec.view_roles)
    if not edit_roles:
        edit_roles = _normalize_roles(default_edit_roles or default_spec.edit_roles)

    return PagePermissionSpec(
        page_name=page_name,
        label=label,
        view_roles=view_roles,
        edit_roles=edit_roles,
    )


def resolve_view_roles(page_name: str, default_roles: Sequence[str] | None = None) -> tuple[str, ...]:
    permissions = resolve_page_permissions(page_name, default_view_roles=default_roles)
    return permissions.view_roles


def resolve_edit_roles(page_name: str, default_roles: Sequence[str] | None = None) -> tuple[str, ...]:
    permissions = resolve_page_permissions(page_name, default_edit_roles=default_roles)
    return permissions.edit_roles


def current_principal_roles() -> tuple[str, ...]:
    if not current_user.is_authenticated:
        return ("public",)
    try:
        roles = getattr(current_user, "roles", [])
        return tuple(sorted({role.name for role in roles}))
    except Exception:  # pragma: no cover - defensive fallback
        return ()


def principal_has_any_role(role_names: Sequence[str], *, require_auth: bool = False) -> bool:
    if not role_names:
        return False
    if require_auth and not current_user.is_authenticated:
        return False
    if not current_user.is_authenticated:
        return any(role == "public" for role in role_names)
    try:
        return current_user.has_any_role(role_names)
    except Exception:  # pragma: no cover - defensive fallback
        return False


def ensure_page_access(
    page_name: str,
    default_roles: Sequence[str] | None = None,
    *,
    default_edit_roles: Sequence[str] | None = None,
):
    """Abort if the current principal is not allowed to access the page."""

    endpoint = request.endpoint or ""
    if endpoint.endswith(".static"):
        return None

    permissions = resolve_page_permissions(
        page_name,
        default_view_roles=default_roles,
        default_edit_roles=default_edit_roles,
    )

    if request.method in SAFE_METHODS:
        if principal_has_any_role(permissions.view_roles):
            return None
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        abort(403)

    if not current_user.is_authenticated:
        return login_manager.unauthorized()

    if permissions.edit_roles and current_user.has_any_role(permissions.edit_roles):
        return None

    abort(403)


def get_known_pages() -> List[dict[str, object]]:
    """Return metadata about all pages that can have configurable access."""

    pages: dict[str, dict[str, object]] = {}
    for name in DEFAULT_PAGE_ACCESS:
        spec = _default_permissions_for(name)
        pages[name] = {
            "page_name": name,
            "label": spec.label,
            "default_view_roles": spec.view_roles,
            "default_edit_roles": spec.edit_roles,
        }

    if not _database_available():
        ordered_pages = sorted(pages.values(), key=lambda entry: entry["label"].lower())
        return ordered_pages

    db_pages = PageAccessRule.query.order_by(PageAccessRule.page_name).all()
    for rule in db_pages:
        spec = resolve_page_permissions(rule.page_name)
        pages[rule.page_name] = {
            "page_name": rule.page_name,
            "label": spec.label,
            "default_view_roles": _default_permissions_for(rule.page_name).view_roles,
            "default_edit_roles": _default_permissions_for(rule.page_name).edit_roles,
            "configured_view_roles": spec.view_roles,
            "configured_edit_roles": spec.edit_roles,
        }

    ordered_pages = sorted(pages.values(), key=lambda entry: entry["label"].lower())
    return ordered_pages


def update_page_permissions(
    page_name: str,
    view_role_ids: Iterable[int],
    edit_role_ids: Iterable[int],
    *,
    label: str | None = None,
) -> None:
    """Persist role assignments for a page."""

    if not _database_available():  # pragma: no cover - defensive guard
        raise RuntimeError("Cannot update page permissions while the database is unavailable.")

    view_role_id_set = set(view_role_ids)
    edit_role_id_set = set(edit_role_ids)

    if edit_role_id_set - view_role_id_set:
        view_role_id_set |= edit_role_id_set

    view_roles = list(
        Role.query.filter(Role.id.in_(view_role_id_set)).order_by(Role.name)
    )
    edit_roles = list(
        Role.query.filter(Role.id.in_(edit_role_id_set)).order_by(Role.name)
    )

    rule = PageAccessRule.query.filter_by(page_name=page_name).first()

    if not view_roles and not edit_roles:
        if rule is not None:
            db.session.delete(rule)
        return

    if rule is None:
        rule = PageAccessRule(
            page_name=page_name,
            label=label or page_name.replace("_", " ").title(),
        )
        db.session.add(rule)
    elif label and rule.label != label:
        rule.label = label

    rule.view_roles = view_roles
    rule.edit_roles = edit_roles


def update_page_roles(
    page_name: str,
    role_ids: Iterable[int],
    *,
    label: str | None = None,
) -> None:
    """Backward-compatible alias for legacy callers."""

    if not _database_available():  # pragma: no cover - defensive guard
        raise RuntimeError("Cannot update page roles while the database is unavailable.")

    update_page_permissions(page_name, role_ids, [], label=label)
