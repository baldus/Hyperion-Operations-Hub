import json

import click

from .services import get_import_issue_schema_signature, is_import_issue_schema_valid


def register_cli(app):
    @app.cli.command("diagnose-import-schema")
    def diagnose_import_schema() -> None:
        """Check the import issue schema for JSONB/TEXT fields."""
        signature = get_import_issue_schema_signature()
        click.echo("Inventory snapshot import issue schema:")
        click.echo(json.dumps(signature, indent=2, sort_keys=True, default=str))
        if is_import_issue_schema_valid(signature):
            click.echo("Schema OK: row_data is JSONB/JSON/TEXT and primary/secondary values are TEXT.")
            return
        click.echo("Schema INVALID: run alembic -c alembic.ini upgrade head.")
        raise SystemExit(1)
