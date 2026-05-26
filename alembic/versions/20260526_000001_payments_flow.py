"""payments flow

Revision ID: 20260526_000001
Revises: 20260424_000004
Create Date: 2026-05-26 00:00:01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260526_000001"
down_revision: Union[str, None] = "20260424_000004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("payments", sa.Column("external_reference", sa.String(length=255), nullable=True))
    op.add_column("payments", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.add_column("payments", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_index("ix_payments_due_date", "payments", ["due_date"], unique=False)
    op.create_index("ix_payments_external_reference", "payments", ["external_reference"], unique=False)
    op.create_index("ix_payments_system_status_created", "payments", ["from_system", "payment_status", "created_at"], unique=False)
    op.create_index("ix_payments_system_external_reference", "payments", ["from_system", "external_reference"], unique=False)
    op.alter_column(
        "internal_webhook_deliveries",
        "subscription_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "internal_webhook_deliveries",
        "subscription_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_index("ix_payments_system_external_reference", table_name="payments")
    op.drop_index("ix_payments_system_status_created", table_name="payments")
    op.drop_index("ix_payments_external_reference", table_name="payments")
    op.drop_index("ix_payments_due_date", table_name="payments")
    op.drop_column("payments", "updated_at")
    op.drop_column("payments", "created_at")
    op.drop_column("payments", "external_reference")
    op.drop_column("payments", "due_date")
