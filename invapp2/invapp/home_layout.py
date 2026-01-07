from __future__ import annotations

from typing import Iterable

from sqlalchemy.exc import SQLAlchemyError

from invapp.extensions import db
from invapp.home_cubes import DEFAULT_HOME_CUBE_KEYS, available_cubes_for_user, cube_payload
from invapp.models import UserHomeLayout


LayoutEntry = dict[str, object]


def allowed_home_cube_keys() -> list[str]:
    return [cube.key for cube in available_cubes_for_user()]


def _normalize_saved_layout(
    raw_layout: object,
    allowed_keys: Iterable[str],
    *,
    default_keys: Iterable[str],
) -> list[LayoutEntry]:
    allowed_key_list = list(allowed_keys)
    allowed_key_set = set(allowed_key_list)
    default_key_list = [key for key in default_keys if key in allowed_key_set]

    layout: list[LayoutEntry] = []
    seen: set[str] = set()

    if isinstance(raw_layout, list):
        for entry in raw_layout:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key")
            if not isinstance(key, str):
                continue
            if key not in allowed_key_set or key in seen:
                continue
            visible = entry.get("visible", True)
            layout.append({"key": key, "visible": bool(visible)})
            seen.add(key)

    if layout:
        base_order = [entry["key"] for entry in layout if isinstance(entry.get("key"), str)]
    else:
        base_order = default_key_list

    for key in base_order:
        if key in seen:
            continue
        layout.append({"key": key, "visible": True})
        seen.add(key)

    for key in allowed_key_list:
        if key in seen:
            continue
        layout.append({"key": key, "visible": False})
        seen.add(key)

    return layout


def build_home_layout_response(user) -> dict[str, list[dict[str, object]]]:
    cubes = available_cubes_for_user()
    cube_map = {cube.key: cube for cube in cubes}
    allowed_keys = [cube.key for cube in cubes]

    raw_layout = None
    if getattr(user, "is_authenticated", False):
        try:
            record = UserHomeLayout.query.filter_by(user_id=user.id).first()
        except SQLAlchemyError:
            db.session.rollback()
            record = None
        if record is not None:
            raw_layout = record.layout_json

    layout = _normalize_saved_layout(
        raw_layout,
        allowed_keys,
        default_keys=DEFAULT_HOME_CUBE_KEYS,
    )

    response_layout: list[dict[str, object]] = []
    available_cubes: list[dict[str, object]] = []

    for index, entry in enumerate(layout):
        key = entry.get("key")
        if not isinstance(key, str):
            continue
        cube = cube_map.get(key)
        if cube is None:
            continue
        payload = cube_payload(cube)
        visible = bool(entry.get("visible", False))
        payload.update({"visible": visible, "order": index})
        response_layout.append(payload)
        if not visible:
            available_cubes.append(payload)

    return {"layout": response_layout, "available_cubes": available_cubes}


def normalize_layout_payload(
    payload: object,
    allowed_keys: Iterable[str],
) -> tuple[list[LayoutEntry] | None, list[str]]:
    errors: list[str] = []
    if not isinstance(payload, list):
        return None, ["Layout payload must be a list."]

    allowed_key_list = list(allowed_keys)
    allowed_key_set = set(allowed_key_list)
    seen: set[str] = set()
    normalized: list[LayoutEntry] = []

    for entry in payload:
        if not isinstance(entry, dict):
            errors.append("Each layout entry must be an object.")
            continue
        key = entry.get("key")
        if not isinstance(key, str):
            errors.append("Each layout entry must include a key string.")
            continue
        if key not in allowed_key_set:
            errors.append(f"Unknown cube key: {key}.")
            continue
        if key in seen:
            errors.append(f"Duplicate cube key: {key}.")
            continue
        visible = entry.get("visible", True)
        if not isinstance(visible, bool):
            errors.append(f"Visible flag must be boolean for {key}.")
            continue
        normalized.append({"key": key, "visible": visible})
        seen.add(key)

    if errors:
        return None, errors

    for key in allowed_key_list:
        if key in seen:
            continue
        normalized.append({"key": key, "visible": False})

    return normalized, []


def save_home_layout(user_id: int, layout: list[LayoutEntry]) -> None:
    record = UserHomeLayout.query.filter_by(user_id=user_id).first()
    if record is None:
        record = UserHomeLayout(user_id=user_id, layout_json=layout)
        db.session.add(record)
    else:
        record.layout_json = layout
    db.session.commit()
