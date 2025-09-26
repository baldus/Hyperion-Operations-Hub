"""Helpers for managing per-page access control settings."""

from __future__ import annotations

from typing import Iterable, List, Sequence

from flask import abort, request

from invapp.extensions import login_manager
from invapp.login import current_user
from invapp.models import PageAccessRule, Role, db

DEFAULT_PAGE_ACCESS: dict[str, dict[str, Sequence[str]]] = {
    "inventory": {
        "label": "Inventory Dashboard",
        "roles": ("inventory", "admin"),
    },
    "orders": {
        "label": "Orders Workspace",
        "roles": ("orders", "admin"),
    },
    "production": {
        "label": "Production History",
        "roles": ("production", "admin"),
    },
    "reports": {
        "label": "Reports",
        "roles": ("reports", "admin"),
    },
    "work": {
        "label": "Work Instructions",
        "roles": ("production", "admin"),
    },
    "settings": {
        "label": "Operations Settings",
        "roles": ("admin",),
    },
    "printers": {
        "label": "Printer Tools",
        "roles": ("admin",),
    },
}


def _default_roles_for(page_name: str) -> Sequence[str]:
    page_defaults = DEFAULT_PAGE_ACCESS.get(page_name)
    if page_defaults:
        return page_defaults.get("roles", ("admin",))
    return ("admin",)


def resolve_allowed_roles(page_name: str, default_roles: Sequence[str] | None = None) -> tuple[str, ...]:
    """Return the active roles allowed for a page."""

    rule = PageAccessRule.query.filter_by(page_name=page_name).first()
    if rule and rule.roles:
        return tuple(sorted({role.name for role in rule.roles}))

    if default_roles:
        return tuple(dict.fromkeys(default_roles))

    return tuple(dict.fromkeys(_default_roles_for(page_name)))


def ensure_page_access(page_name: str, default_roles: Sequence[str] | None = None):
    """Abort if the current user is not allowed to access the page."""

    endpoint = request.endpoint or ""
    if endpoint.endswith(".static"):
        return None

    if not current_user.is_authenticated:
        return login_manager.unauthorized()

    allowed_roles = resolve_allowed_roles(page_name, default_roles=default_roles)
    if allowed_roles and current_user.has_any_role(allowed_roles):
        return None

    abort(403)


def get_known_pages() -> List[dict[str, object]]:
    """Return metadata about all pages that can have configurable access."""

    pages: dict[str, dict[str, object]] = {}
    for name, config in DEFAULT_PAGE_ACCESS.items():
        pages[name] = {
            "page_name": name,
            "label": config.get("label", name.title()),
            "default_roles": tuple(config.get("roles", ("admin",))),
        }

    db_pages = PageAccessRule.query.order_by(PageAccessRule.page_name).all()
    for rule in db_pages:
        pages.setdefault(
            rule.page_name,
            {
                "page_name": rule.page_name,
                "label": rule.label or rule.page_name.replace("_", " ").title(),
                "default_roles": tuple(_default_roles_for(rule.page_name)),
            },
        )

    ordered_pages = sorted(pages.values(), key=lambda entry: entry["label"].lower())
    return ordered_pages


def update_page_roles(page_name: str, role_ids: Iterable[int], *, label: str | None = None) -> None:
    """Persist role assignments for a page."""

    roles = list(Role.query.filter(Role.id.in_(set(role_ids))).order_by(Role.name))
    rule = PageAccessRule.query.filter_by(page_name=page_name).first()

    if not roles:
        if rule is not None:
            db.session.delete(rule)
        return

    if rule is None:
        rule = PageAccessRule(page_name=page_name, label=label or page_name.replace("_", " ").title())
        db.session.add(rule)
    elif label and rule.label != label:
        rule.label = label

    rule.roles = roles
