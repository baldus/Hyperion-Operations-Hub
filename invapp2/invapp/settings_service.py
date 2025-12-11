from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Dict

from flask import g

from invapp.extensions import db
from invapp.models import AppSetting


DECIMAL_QUANT = Decimal("0.01")
MAX_OFFSET = Decimal("12")


def _get_cache() -> Dict[str, Decimal]:
    cache: Dict[str, Decimal] = getattr(g, "_app_settings_cache", {})
    if not getattr(g, "_app_settings_cache", None):
        g._app_settings_cache = cache
    return cache


def get_decimal(key: str, default: Decimal | None = None) -> Decimal:
    cache = _get_cache()
    if key in cache:
        return cache[key]

    default_value = Decimal("0") if default is None else default
    setting = AppSetting.query.filter_by(key=key).first()
    if setting is None or setting.value is None:
        cache[key] = default_value
        return default_value

    try:
        value = Decimal(setting.value)
    except (InvalidOperation, TypeError):
        value = default_value

    cache[key] = value
    return value


def set_decimal(key: str, value: Decimal | float | int | str, user_id: int | None) -> AppSetting:
    try:
        normalized = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid number for the offset.")

    if normalized < 0:
        raise ValueError("Offset cannot be negative.")
    if normalized > MAX_OFFSET:
        raise ValueError("Offset cannot exceed 12.0.")

    normalized = normalized.quantize(DECIMAL_QUANT)

    setting = AppSetting.query.filter_by(key=key).first()
    if setting is None:
        setting = AppSetting(key=key)
        db.session.add(setting)

    setting.value = str(normalized)
    setting.updated_by_id = user_id
    setting.updated_at = datetime.utcnow()

    db.session.commit()

    cache = _get_cache()
    cache[key] = normalized
    return setting
