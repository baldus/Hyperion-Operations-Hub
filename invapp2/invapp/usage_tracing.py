from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from flask import Flask, request, template_rendered

DEFAULT_LOG_NAME = "usage_tracing.log"


def _coerce_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def init_usage_tracing(app: Flask) -> None:
    enabled = app.config.get("ENABLE_USAGE_TRACING")
    if isinstance(enabled, str):
        enabled = _coerce_bool(enabled)
    if enabled is None:
        enabled = _coerce_bool(os.getenv("ENABLE_USAGE_TRACING"))

    if not enabled:
        return

    log_path = Path(
        app.config.get("USAGE_TRACE_LOG_PATH", Path(app.instance_path) / DEFAULT_LOG_NAME)
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("usage_tracing")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", "") == str(log_path)
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=5)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)

    def emit(event: str, payload: dict[str, Any]) -> None:
        logger.info(json.dumps({"event": event, **payload}, sort_keys=True))

    @app.before_request
    def _log_request_usage() -> None:
        rule = request.url_rule.rule if request.url_rule else None
        emit(
            "request",
            {
                "endpoint": request.endpoint,
                "rule": rule,
                "blueprint": request.blueprint,
                "method": request.method,
                "path": request.path,
            },
        )

    @app.after_request
    def _log_static_usage(response):
        if request.path.startswith("/static/") or request.endpoint == "static":
            emit(
                "static",
                {
                    "path": request.path,
                    "status": response.status_code,
                    "method": request.method,
                },
            )
        return response

    def _record_template(sender, template, context, **extra):
        template_name = getattr(template, "name", None)
        if template_name:
            emit(
                "template",
                {
                    "template": template_name,
                    "endpoint": request.endpoint,
                    "path": request.path,
                },
            )

    template_rendered.connect(_record_template, app)
