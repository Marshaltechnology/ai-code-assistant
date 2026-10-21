"""add composite usage-ledger indexes

Revision ID: e9f0a1b2c3d4
Revises: c7d8e9f0a1b2
Create Date: 2026-10-21 00:00:00.000000

Phase 10: keep period aggregates and dashboard queries indexed as the ledger
grows (#436). The single-column indexes remain for narrow lookups; these
composites cover the common ``owner/workspace + created_at`` and
``owner/workspace + source + created_at`` query shapes.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e9f0a1b2c3d4"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_usage_records_user_created_at",
        "usage_records",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_usage_records_workspace_created_at",
        "usage_records",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_usage_records_user_source_created_at",
        "usage_records",
        ["user_id", "source", "created_at"],
    )
    op.create_index(
        "ix_usage_records_workspace_source_created_at",
        "usage_records",
        ["workspace_id", "source", "created_at"],
    )


def downgrade():
    op.drop_index("ix_usage_records_workspace_source_created_at", table_name="usage_records")
    op.drop_index("ix_usage_records_user_source_created_at", table_name="usage_records")
    op.drop_index("ix_usage_records_workspace_created_at", table_name="usage_records")
    op.drop_index("ix_usage_records_user_created_at", table_name="usage_records")
