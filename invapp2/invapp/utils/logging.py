from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from flask import Flask, g, has_request_context


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        request_id = None
        if has_request_context():
            request_id = getattr(g, "request_id", None)
        record.request_id = request_id or "-"
        return True


def _has_handler(logger: logging.Logger, handler_types: Iterable[type]) -> bool:
    return any(isinstance(handler, handler_types) for handler in logger.handlers)


def configure_logging(app: Flask) -> Path:
    logs_dir = Path(app.root_path).parent / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "hyperion_ops_console.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [req=%(request_id)s] %(name)s: %(message)s"
    )
    request_filter = RequestIdFilter()

    if not _has_handler(root_logger, (logging.StreamHandler,)):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(request_filter)
        root_logger.addHandler(stream_handler)

    if not any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", "") == str(log_path)
        for handler in root_logger.handlers
    ):
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        root_logger.addHandler(file_handler)

    for handler in app.logger.handlers:
        if request_filter not in handler.filters:
            handler.addFilter(request_filter)

    app.logger.setLevel(logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    logging.getLogger("gunicorn.error").setLevel(logging.INFO)
    logging.getLogger("gunicorn.access").setLevel(logging.INFO)

    return log_path
