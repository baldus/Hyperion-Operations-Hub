from __future__ import annotations

from dataclasses import dataclass

from flask import url_for

from invapp.permissions import principal_has_any_role, resolve_view_roles


@dataclass(frozen=True)
class CubeDefinition:
    key: str
    display_name: str
    description: str
    endpoint: str
    permission_page: str | None = None


HOME_CUBES: tuple[CubeDefinition, ...] = (
    CubeDefinition(
        key="orders",
        display_name="Orders",
        description="Production deadlines and active order risks.",
        endpoint="orders.orders_home",
        permission_page="orders",
    ),
    CubeDefinition(
        key="inventory",
        display_name="Inventory",
        description="Stock alerts and minimum-level exceptions.",
        endpoint="inventory.inventory_home",
        permission_page="inventory",
    ),
    CubeDefinition(
        key="incoming_items",
        display_name="Incoming Items",
        description="Purchasing arrivals due soon and overdue.",
        endpoint="purchasing.purchasing_home",
        permission_page="purchasing",
    ),
)

DEFAULT_HOME_CUBE_KEYS: tuple[str, ...] = (
    "orders",
    "inventory",
    "incoming_items",
)


def available_cubes_for_user() -> list[CubeDefinition]:
    cubes: list[CubeDefinition] = []
    for cube in HOME_CUBES:
        if cube.permission_page:
            view_roles = resolve_view_roles(cube.permission_page)
            if not principal_has_any_role(view_roles):
                continue
        cubes.append(cube)
    return cubes


def cube_payload(cube: CubeDefinition) -> dict[str, str | None]:
    href = None
    try:
        href = url_for(cube.endpoint)
    except Exception:  # pragma: no cover - defensive fallback
        href = None
    return {
        "key": cube.key,
        "display_name": cube.display_name,
        "description": cube.description,
        "href": href,
    }
