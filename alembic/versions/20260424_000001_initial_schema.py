"""initial schema

Revision ID: 20260424_000001
Revises:
Create Date: 2026-04-24 00:00:01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260424_000001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nome", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("cpf", sa.String(length=11), nullable=True),
        sa.Column("cnpj", sa.String(length=14), nullable=True),
        sa.Column("provider_customer_id", sa.String(), nullable=True),
        sa.Column("system_customer_id", sa.String(), nullable=False),
        sa.Column("gateway_provider", sa.String(length=50), nullable=False),
        sa.Column("system", sa.String(length=50), nullable=False),
        sa.CheckConstraint("(cpf IS NOT NULL) <> (cnpj IS NOT NULL)", name="ck_customers_single_document"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_customer_id", name="uq_customers_provider_ref"),
        sa.UniqueConstraint("system_customer_id", "system", name="uq_customers_system_ref"),
    )
    op.create_index(op.f("ix_customers_provider_customer_id"), "customers", ["provider_customer_id"], unique=False)
    op.create_index(op.f("ix_customers_system"), "customers", ["system"], unique=False)
    op.create_index(op.f("ix_customers_system_customer_id"), "customers", ["system_customer_id"], unique=False)

    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("initial_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("system_subscription_id", sa.String(), nullable=False),
        sa.Column("gateway_subscription_id", sa.String(), nullable=False),
        sa.Column("gateway_provider", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("last_paid_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("from_system", sa.String(length=50), nullable=False),
        sa.Column("subscription_type", sa.String(length=50), nullable=False),
        sa.Column("value", sa.DECIMAL(precision=18, scale=2), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial_days", sa.Integer(), nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_link", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gateway_subscription_id", name="uq_subscriptions_gateway_ref"),
        sa.UniqueConstraint("system_subscription_id", "from_system", name="uq_subscriptions_system_ref"),
    )
    op.create_index(op.f("ix_subscriptions_from_system"), "subscriptions", ["from_system"], unique=False)
    op.create_index(op.f("ix_subscriptions_gateway_subscription_id"), "subscriptions", ["gateway_subscription_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"], unique=False)
    op.create_index(op.f("ix_subscriptions_subscription_type"), "subscriptions", ["subscription_type"], unique=False)

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("gateway", sa.String(length=50), nullable=False),
        sa.Column("system_payment_id", sa.String(), nullable=False),
        sa.Column("provider_payment_id", sa.String(), nullable=True),
        sa.Column("net_value", sa.DECIMAL(precision=18, scale=2), nullable=True),
        sa.Column("value", sa.DECIMAL(precision=18, scale=2), nullable=False),
        sa.Column("from_system", sa.String(length=50), nullable=False),
        sa.Column("payment_status", sa.String(length=50), nullable=False),
        sa.Column("payment_type", sa.String(length=50), nullable=False),
        sa.Column("paid_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("movimentation_type", sa.String(length=50), nullable=False),
        sa.Column("checkout_link", sa.String(), nullable=True),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("webhook_link", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_payment_id", name="uq_payments_provider_ref"),
        sa.UniqueConstraint("system_payment_id", name="uq_payments_system_ref"),
    )
    op.create_index(op.f("ix_payments_from_system"), "payments", ["from_system"], unique=False)
    op.create_index(op.f("ix_payments_gateway"), "payments", ["gateway"], unique=False)
    op.create_index(op.f("ix_payments_paid_date"), "payments", ["paid_date"], unique=False)

    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_index(op.f("ix_payments_paid_date"), table_name="payments")
    op.drop_index(op.f("ix_payments_gateway"), table_name="payments")
    op.drop_index(op.f("ix_payments_from_system"), table_name="payments")
    op.drop_table("payments")
    op.drop_index(op.f("ix_subscriptions_subscription_type"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_status"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_gateway_subscription_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_from_system"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_customers_system_customer_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_system"), table_name="customers")
    op.drop_index(op.f("ix_customers_provider_customer_id"), table_name="customers")
    op.drop_table("customers")
