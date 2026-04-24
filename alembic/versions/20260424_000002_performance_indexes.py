"""performance indexes

Revision ID: 20260424_000002
Revises: 20260424_000001
Create Date: 2026-04-24 00:00:02
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260424_000002"
down_revision: Union[str, None] = "20260424_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_subscriptions_system_status_due",
        "subscriptions",
        ["from_system", "status", "next_due_date"],
        unique=False,
    )
    op.create_index(
        "ix_payments_subscription_paid_date",
        "payments",
        ["subscription_id", "paid_date"],
        unique=False,
    )
    op.create_index(
        "ix_payments_system_status_paid_date",
        "payments",
        ["from_system", "payment_status", "paid_date"],
        unique=False,
    )
    op.create_index(
        "ix_payments_payment_status",
        "payments",
        ["payment_status"],
        unique=False,
    )
    op.create_index(
        "ix_customers_system_gateway",
        "customers",
        ["system", "gateway_provider"],
        unique=False,
    )
    op.create_index(
        "ix_webhook_events_provider",
        "webhook_events",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "ix_webhook_events_processed",
        "webhook_events",
        ["processed"],
        unique=False,
    )
    op.create_index(
        "ix_webhook_events_created_at",
        "webhook_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_webhook_events_provider_processed_created",
        "webhook_events",
        ["provider", "processed", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_events_provider_processed_created", table_name="webhook_events")
    op.drop_index("ix_webhook_events_created_at", table_name="webhook_events")
    op.drop_index("ix_webhook_events_processed", table_name="webhook_events")
    op.drop_index("ix_webhook_events_provider", table_name="webhook_events")
    op.drop_index("ix_customers_system_gateway", table_name="customers")
    op.drop_index("ix_payments_payment_status", table_name="payments")
    op.drop_index("ix_payments_system_status_paid_date", table_name="payments")
    op.drop_index("ix_payments_subscription_paid_date", table_name="payments")
    op.drop_index("ix_subscriptions_system_status_due", table_name="subscriptions")
