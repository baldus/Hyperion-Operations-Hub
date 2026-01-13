"""CSV export utilities."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

from flask import Response, stream_with_context


def _serialize_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def export_rows_to_csv(
    rows: Iterable[object],
    columns: Iterable[tuple[str, str]],
    filename: str,
) -> Response:
    headers = [header for _, header in columns]

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for row in rows:
            row_values = []
            for field, _ in columns:
                if isinstance(row, dict):
                    value = row.get(field)
                else:
                    value = getattr(row, field, None)
                row_values.append(_serialize_value(value))
            writer.writerow(row_values)
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    response = Response(stream_with_context(generate()), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response
