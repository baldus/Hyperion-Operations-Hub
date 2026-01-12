import argparse
import json
import logging
from dataclasses import asdict
from typing import Any

from sqlalchemy import create_engine, inspect, text

from config import Config
from invapp.db_maintenance import analyze_primary_key_sequences, repair_primary_key_sequences
from invapp.extensions import db

# Ensure models are registered with the declarative base
import invapp.models  # noqa: F401


LOGGER_NAME = "invapp.db_sanity_check"


def _build_engine(config: Config):
    return create_engine(
        config.SQLALCHEMY_DATABASE_URI,
        pool_pre_ping=True,
    )


def _format_issue(issue) -> str:
    current = issue.current_next_value
    desired = issue.desired_next_value
    prefix = f"{issue.table} ({issue.pk_column})"
    if issue.sequence_name:
        prefix += f" -> {issue.sequence_name}"
    if issue.error:
        return f"{prefix}: ERROR {issue.error}"
    if issue.status == "mismatch":
        return f"{prefix}: sequence at {current} but needs to be at least {desired}"
    if issue.status == "skipped":
        return f"{prefix}: skipped (no sequence)"
    return f"{prefix}: status={issue.status}"


def _batch_soft_delete_status(engine) -> dict[str, Any]:
    inspector = inspect(engine)
    status: dict[str, Any] = {
        "table_present": False,
        "column_present": False,
        "index_present": False,
        "active_count": None,
        "removed_count": None,
    }

    try:
        status["table_present"] = "batch" in inspector.get_table_names()
    except Exception:
        return status

    if not status["table_present"]:
        return status

    try:
        columns = {col["name"] for col in inspector.get_columns("batch")}
    except Exception:
        return status

    status["column_present"] = "removed_at" in columns

    try:
        indexes = inspector.get_indexes("batch")
    except Exception:
        indexes = []
    status["index_present"] = any(
        index.get("column_names") == ["removed_at"] for index in indexes
    )

    if not status["column_present"]:
        return status

    with engine.connect() as connection:
        result = connection.execute(
            text(
                "SELECT "
                "SUM(CASE WHEN removed_at IS NULL THEN 1 ELSE 0 END) AS active_count, "
                "SUM(CASE WHEN removed_at IS NOT NULL THEN 1 ELSE 0 END) AS removed_count "
                "FROM batch"
            )
        )
        row = result.mappings().first()
        if row:
            status["active_count"] = int(row["active_count"] or 0)
            status["removed_count"] = int(row["removed_count"] or 0)

    return status


def run_check(apply_fixes: bool, as_json_output: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(LOGGER_NAME)

    config = Config()
    engine = _build_engine(config)

    checks = analyze_primary_key_sequences(engine, db.Model, logger=logger)
    issues = [c for c in checks if c.status != "ok"]
    batch_soft_delete = _batch_soft_delete_status(engine)

    repair_summary: dict[str, Any] | None = None
    exit_code = 0

    if issues and not apply_fixes:
        exit_code = 1

    if apply_fixes:
        repair_summary = repair_primary_key_sequences(engine, db.Model, logger=logger)
        if repair_summary.get("failed"):
            exit_code = 2

    if as_json_output:
        payload = {
            "issues": [asdict(c) for c in issues],
            "repair_summary": repair_summary,
            "batch_soft_delete": batch_soft_delete,
        }
        print(json.dumps(payload, indent=2))
    else:
        if not issues:
            print("✅ All primary key sequences appear healthy")
        else:
            print("⚠️ Sequence issues detected:")
            for issue in issues:
                print(f" - {_format_issue(issue)}")
        if repair_summary:
            print(
                f"Repair summary: repaired={repair_summary.get('repaired', 0)} "
                f"skipped={repair_summary.get('skipped', 0)} failed={repair_summary.get('failed', 0)}"
            )
        if not batch_soft_delete["table_present"]:
            print("⚠️ Batch table not found; skipped soft-delete column check.")
        elif not batch_soft_delete["column_present"]:
            print("⚠️ Batch.removed_at column is missing.")
        else:
            print(
                "Batch soft-delete status: "
                f"active={batch_soft_delete['active_count']} "
                f"removed={batch_soft_delete['removed_count']} "
                f"index={'yes' if batch_soft_delete['index_present'] else 'no'}"
            )

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Check database primary key sequences")
    parser.add_argument("--fix", action="store_true", help="Apply repairs instead of reporting only")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON output")
    args = parser.parse_args()

    raise SystemExit(run_check(apply_fixes=args.fix, as_json_output=args.json_output))


if __name__ == "__main__":
    main()
