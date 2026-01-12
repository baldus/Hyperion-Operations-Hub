from __future__ import annotations

from typing import Dict, Tuple

from sqlalchemy import inspect

from invapp.extensions import db

_COLUMN_CACHE: Dict[Tuple[str, str, str], bool] = {}


def db_has_column(table_name: str, column_name: str) -> bool:
    engine = db.engine
    cache_key = (str(engine.url), table_name, column_name)
    if cache_key in _COLUMN_CACHE:
        return _COLUMN_CACHE[cache_key]

    try:
        columns = {column["name"] for column in inspect(engine).get_columns(table_name)}
    except Exception:
        _COLUMN_CACHE[cache_key] = False
        return False

    result = column_name in columns
    _COLUMN_CACHE[cache_key] = result
    return result
