import argparse
import json
import logging

from sqlalchemy import create_engine, inspect, text

from config import Config


LOGGER_NAME = "invapp.batch_soft_delete_check"


def _build_engine(config: Config):
    return create_engine(
        config.SQLALCHEMY_DATABASE_URI,
        pool_pre_ping=True,
    )


def run_check(as_json_output: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(LOGGER_NAME)

    config = Config()
    engine = _build_engine(config)
    payload = {
        "has_batch_table": False,
        "has_removed_at": False,
        "active_count": None,
        "removed_count": None,
    }
    exit_code = 0

    try:
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        payload["has_batch_table"] = "batch" in table_names
        if not payload["has_batch_table"]:
            exit_code = 1
            return _emit_results(payload, as_json_output, logger, exit_code)

        columns = {col["name"] for col in inspector.get_columns("batch")}
        payload["has_removed_at"] = "removed_at" in columns
        if not payload["has_removed_at"]:
            exit_code = 2
            return _emit_results(payload, as_json_output, logger, exit_code)

        with engine.connect() as connection:
            result = connection.execute(
                text(
                    "SELECT "
                    "SUM(CASE WHEN removed_at IS NULL THEN 1 ELSE 0 END) AS active_count, "
                    "SUM(CASE WHEN removed_at IS NOT NULL THEN 1 ELSE 0 END) AS removed_count "
                    "FROM batch"
                )
            )
            row = result.first()
            if row:
                payload["active_count"] = int(row.active_count or 0)
                payload["removed_count"] = int(row.removed_count or 0)
    finally:
        engine.dispose()

    return _emit_results(payload, as_json_output, logger, exit_code)


def _emit_results(payload: dict, as_json_output: bool, logger, exit_code: int) -> int:
    if as_json_output:
        print(json.dumps(payload, indent=2))
        return exit_code

    if not payload["has_batch_table"]:
        logger.warning("⚠️ batch table not found in the configured database.")
        return exit_code

    if not payload["has_removed_at"]:
        logger.warning("⚠️ batch.removed_at column is missing.")
        return exit_code

    logger.info("✅ batch.removed_at column exists.")
    logger.info(
        "Active batches: %s | Removed batches: %s",
        payload["active_count"],
        payload["removed_count"],
    )
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify batch.removed_at soft-delete support."
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    raise SystemExit(run_check(as_json_output=args.json_output))


if __name__ == "__main__":
    main()
