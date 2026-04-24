"""gateway operations and internal webhook deliveries

Revision ID: 20260424_000003
Revises: 20260424_000002
Create Date: 2026-04-24 00:00:03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260424_000003"
down_revision: Union[str, None] = "20260424_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gateway_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_name", sa.String(length=100), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("system", sa.String(length=50), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("gateway_reference", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_gateway_operations_provider", "gateway_operations", ["provider"], unique=False)
    op.create_index("ix_gateway_operations_system", "gateway_operations", ["system"], unique=False)
    op.create_index("ix_gateway_operations_status", "gateway_operations", ["status"], unique=False)
    op.create_index("ix_gateway_operations_gateway_reference", "gateway_operations", ["gateway_reference"], unique=False)
    op.create_index("ix_gateway_operations_created_at", "gateway_operations", ["created_at"], unique=False)
    op.create_index(
        "ix_gateway_operations_system_status_created",
        "gateway_operations",
        ["system", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "internal_webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("target_url", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_internal_webhook_deliveries_event_type", "internal_webhook_deliveries", ["event_type"], unique=False)
    op.create_index("ix_internal_webhook_deliveries_subscription_id", "internal_webhook_deliveries", ["subscription_id"], unique=False)
    op.create_index("ix_internal_webhook_deliveries_payment_id", "internal_webhook_deliveries", ["payment_id"], unique=False)
    op.create_index("ix_internal_webhook_deliveries_status", "internal_webhook_deliveries", ["status"], unique=False)
    op.create_index("ix_internal_webhook_deliveries_created_at", "internal_webhook_deliveries", ["created_at"], unique=False)
    op.create_index(
        "ix_internal_webhook_deliveries_status_created",
        "internal_webhook_deliveries",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_internal_webhook_deliveries_status_created", table_name="internal_webhook_deliveries")
    op.drop_index("ix_internal_webhook_deliveries_created_at", table_name="internal_webhook_deliveries")
    op.drop_index("ix_internal_webhook_deliveries_status", table_name="internal_webhook_deliveries")
    op.drop_index("ix_internal_webhook_deliveries_payment_id", table_name="internal_webhook_deliveries")
    op.drop_index("ix_internal_webhook_deliveries_subscription_id", table_name="internal_webhook_deliveries")
    op.drop_index("ix_internal_webhook_deliveries_event_type", table_name="internal_webhook_deliveries")
    op.drop_table("internal_webhook_deliveries")

    op.drop_index("ix_gateway_operations_system_status_created", table_name="gateway_operations")
    op.drop_index("ix_gateway_operations_created_at", table_name="gateway_operations")
    op.drop_index("ix_gateway_operations_gateway_reference", table_name="gateway_operations")
    op.drop_index("ix_gateway_operations_status", table_name="gateway_operations")
    op.drop_index("ix_gateway_operations_system", table_name="gateway_operations")
    op.drop_index("ix_gateway_operations_provider", table_name="gateway_operations")
    op.drop_table("gateway_operations")
