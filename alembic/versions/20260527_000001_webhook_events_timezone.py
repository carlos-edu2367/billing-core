"""make webhook event timestamps timezone-aware

Revision ID: 20260527_000001
Revises: 20260526_000002
Create Date: 2026-05-27 00:00:01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260527_000001"
down_revision: Union[str, None] = "20260526_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "webhook_events",
        "created_at",
        existing_type=sa.DateTime(timezone=False),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="created_at AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    op.alter_column(
        "webhook_events",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(timezone=False),
        existing_nullable=False,
        postgresql_using="created_at AT TIME ZONE 'UTC'",
    )
