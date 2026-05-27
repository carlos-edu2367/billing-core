"""make subscription_id nullable in internal_webhook_deliveries

Revision ID: 20260527_000002
Revises: 20260527_000001
Create Date: 2026-05-27 19:35:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260527_000002"
down_revision: Union[str, None] = "20260527_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Reconfigure the foreign key constraint to use ON DELETE SET NULL
    op.drop_constraint(
        "internal_webhook_deliveries_subscription_id_fkey",
        "internal_webhook_deliveries",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "internal_webhook_deliveries_subscription_id_fkey",
        "internal_webhook_deliveries",
        "subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Revert to original foreign key constraint without ON DELETE SET NULL
    op.drop_constraint(
        "internal_webhook_deliveries_subscription_id_fkey",
        "internal_webhook_deliveries",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "internal_webhook_deliveries_subscription_id_fkey",
        "internal_webhook_deliveries",
        "subscriptions",
        ["subscription_id"],
        ["id"],
    )
