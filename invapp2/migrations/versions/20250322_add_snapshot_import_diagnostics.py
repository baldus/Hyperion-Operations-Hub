"""Add snapshot import diagnostics table.

Revision ID: 20250322_add_snapshot_import_diagnostics
Revises: 20250321_safe_row_data_jsonb
Create Date: 2025-03-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20250322_add_snapshot_import_diagnostics"
down_revision = "20250321_safe_row_data_jsonb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    json_type = postgresql.JSONB() if bind.dialect.name == "postgresql" else sa.JSON()

    op.create_table(
        "inventory_snapshot_import_diagnostics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("inventory_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("row_count_total", sa.Integer(), nullable=True),
        sa.Column("row_count_processed", sa.Integer(), nullable=True),
        sa.Column("issue_count_total", sa.Integer(), nullable=True),
        sa.Column("issue_counts_by_reason", json_type, nullable=True),
        sa.Column("max_primary_len", sa.Integer(), nullable=True),
        sa.Column("max_secondary_len", sa.Integer(), nullable=True),
        sa.Column("max_row_data_bytes", sa.Integer(), nullable=True),
        sa.Column("p95_row_data_bytes", sa.Integer(), nullable=True),
        sa.Column("row_data_compacted_count", sa.Integer(), nullable=True),
        sa.Column("invalid_json_row_count", sa.Integer(), nullable=True),
        sa.Column("blank_header_count", sa.Integer(), nullable=True),
        sa.Column("unknown_header_count", sa.Integer(), nullable=True),
        sa.Column("top_unknown_headers", json_type, nullable=True),
        sa.Column("schema_signature", json_type, nullable=True),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("parse_time_ms", sa.Integer(), nullable=True),
        sa.Column("match_time_ms", sa.Integer(), nullable=True),
        sa.Column("issue_insert_time_ms", sa.Integer(), nullable=True),
        sa.Column("total_time_ms", sa.Integer(), nullable=True),
        sa.Column("batch_size", sa.Integer(), nullable=True),
        sa.Column("failure_samples", json_type, nullable=True),
        sa.Column(
            "schema_drift_detected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_table("inventory_snapshot_import_diagnostics")
