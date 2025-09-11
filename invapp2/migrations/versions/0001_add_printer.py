"""add printer table

Revision ID: 0001_add_printer
Revises: 
Create Date: 2024-02-15

"""

from alembic import op
import sqlalchemy as sa


revision = "0001_add_printer"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "printer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("connection", sa.String(), nullable=False),
        sa.Column("label_width", sa.Float()),
        sa.Column("label_height", sa.Float()),
    )


def downgrade():
    op.drop_table("printer")

