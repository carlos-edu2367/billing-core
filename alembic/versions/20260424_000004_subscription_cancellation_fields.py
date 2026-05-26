"""subscription cancellation tracking fields

Revision ID: 20260424_000004
Revises: 20260424_000003
Create Date: 2026-04-24 00:00:04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260424_000004"
down_revision: Union[str, None] = "20260424_000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column("cancellation_reason", sa.String(length=500), nullable=True))
    op.add_column("subscriptions", sa.Column("cancellation_job_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("subscriptions", "cancellation_job_id")
    op.drop_column("subscriptions", "cancellation_reason")
    op.drop_column("subscriptions", "cancellation_requested_at")
