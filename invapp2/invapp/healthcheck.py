import argparse
import datetime as dt
import logging
import os
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError

from config import Config
from invapp.db_maintenance import repair_primary_key_sequences
from invapp.extensions import db

# Ensure models are registered with the declarative base
import invapp.models  # noqa: F401


def _mask_db_url(raw_url: str) -> str:
    try:
        parsed = make_url(raw_url)
        if parsed.password:
            parsed = parsed.set(password="***")
        return str(parsed)
    except Exception:
        if "@" in raw_url:
            prefix, remainder = raw_url.split("@", 1)
            if ":" in prefix:
                user, _ = prefix.split(":", 1)
                return f"{user}:***@{remainder}"
        return raw_url


def _render_banner(lines: list[str]) -> str:
    width = max(len(line) for line in lines) + 4
    border = "+" + "-" * (width - 2) + "+"
    body = [f"| {line.ljust(width - 4)} |" for line in lines]
    return "\n".join([border, *body, border])


def _build_engine(config: Config):
    return create_engine(
        config.SQLALCHEMY_DATABASE_URI,
        pool_pre_ping=True,
    )


def _check_database(engine, logger: logging.Logger) -> tuple[bool, str | None]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, None
    except SQLAlchemyError as exc:
        logger.warning("Database connection failed: %s", exc)
        return False, str(exc)


def _sequence_repair(engine, logger: logging.Logger, dry_run: bool) -> dict[str, Any]:
    try:
        return repair_primary_key_sequences(
            engine, db.Model, logger=logger, dry_run=dry_run
        )
    except SQLAlchemyError as exc:
        logger.warning(
            "Sequence repair failed: %s", exc, exc_info=logger.isEnabledFor(logging.DEBUG)
        )
        return {"repaired": 0, "skipped": 0, "failed": 1, "details": [], "error": str(exc)}


def _overall_status(database_ok: bool, sequence_summary: dict[str, Any]) -> str:
    if not database_ok:
        return "FAIL"
    if sequence_summary.get("failed"):
        return "WARN"
    return "OK"


def run_healthcheck(nonfatal: bool, dry_run: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger("invapp.healthcheck")

    config = Config()
    masked_url = _mask_db_url(config.SQLALCHEMY_DATABASE_URI)

    engine = _build_engine(config)
    database_ok, error_message = _check_database(engine, logger)
    sequence_summary = (
        {"repaired": 0, "skipped": 0, "failed": 0, "details": []}
        if not database_ok
        else _sequence_repair(engine, logger, dry_run)
    )

    status = _overall_status(database_ok, sequence_summary)
    timestamp = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    host = os.getenv("HOST", "0.0.0.0")
    port = os.getenv("PORT", os.getenv("GUNICORN_PORT", "8000"))
    workers = os.getenv("GUNICORN_WORKERS", "2")

    lines = [
        f"Hyperion Operations Console Health Check — {timestamp}",
        f"App boot status: {status}",
        f"DB connection: {'OK' if database_ok else 'FAIL'}",
        f"Sequence repair: repaired={sequence_summary.get('repaired', 0)} "
        f"skipped={sequence_summary.get('skipped', 0)} failed={sequence_summary.get('failed', 0)}",
        f"DB_URL: {masked_url}",
        f"Gunicorn: bind={host}:{port} workers={workers}",
    ]

    if error_message:
        lines.append(f"DB error: {error_message}")
    if dry_run:
        lines.append("Sequence repair ran in dry-run mode")

    print(_render_banner(lines))

    if status == "FAIL" and not nonfatal:
        return 1
    if sequence_summary.get("failed") and not nonfatal:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperion Operations Console health check")
    parser.add_argument(
        "--fatal",
        action="store_true",
        help="Exit with non-zero status when checks fail",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect sequences without applying changes",
    )
    args = parser.parse_args()

    exit_code = run_healthcheck(nonfatal=not args.fatal, dry_run=args.dry_run)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
