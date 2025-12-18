from __future__ import annotations

import traceback

from flask import Blueprint, current_app, render_template, request
from werkzeug.exceptions import HTTPException

from invapp import models
from invapp.audit import resolve_client_ip
from invapp.extensions import db
from invapp.login import current_user

bp = Blueprint("errors", __name__)


def _format_stacktrace(error: BaseException | None) -> str:
    if error is None:
        return ""

    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


@bp.app_errorhandler(Exception)
def handle_exception(error: Exception):
    # Allow HTTP errors that are not 500 to propagate to their default handlers.
    if isinstance(error, HTTPException) and error.code != 500:
        return error

    root_error: BaseException | None = getattr(error, "original_exception", None)
    if root_error is None or not isinstance(root_error, BaseException):
        root_error = error if isinstance(error, BaseException) else None

    stacktrace = _format_stacktrace(root_error)
    error_message = "Internal Server Error"
    if isinstance(error, HTTPException) and error.description:
        error_message = error.description
    elif isinstance(error, BaseException):
        error_message = str(error) or error_message

    current_app.logger.exception("Unhandled exception", exc_info=error)

    status_code = 500
    if isinstance(error, HTTPException) and error.code:
        status_code = error.code

    return (
        render_template(
            "errors/server_error.html",
            error_message=error_message,
            stacktrace=stacktrace,
            endpoint=request.endpoint,
            path=request.path,
            report_submitted=False,
            report_failed=False,
        ),
        status_code,
    )


@bp.route("/report", methods=["POST"])
def report_error():
    message = (request.form.get("message") or "Unknown error").strip()
    stacktrace = request.form.get("stacktrace") or ""
    path = request.form.get("path") or request.referrer or None
    endpoint = request.form.get("endpoint") or None

    user_id = None
    username = None
    if getattr(current_user, "is_authenticated", False):
        user_id = getattr(current_user, "id", None)
        username = getattr(current_user, "username", None)

    report = models.ErrorReport(
        message=message,
        stacktrace=stacktrace,
        path=path,
        endpoint=endpoint,
        user_id=user_id,
        username=username,
        user_agent=request.user_agent.string if request.user_agent else None,
        ip_address=resolve_client_ip(),
    )

    saved = False
    try:
        db.session.add(report)
        db.session.commit()
        saved = True
    except Exception:
        current_app.logger.exception("Failed to save error report")
        db.session.rollback()

    return render_template(
        "errors/server_error.html",
        error_message=message,
        stacktrace=stacktrace,
        endpoint=endpoint,
        path=path,
        report_submitted=saved,
        report_failed=not saved,
    )
