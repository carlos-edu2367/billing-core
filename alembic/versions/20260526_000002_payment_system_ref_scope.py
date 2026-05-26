"""scope payment system reference by system

Revision ID: 20260526_000002
Revises: 20260526_000001
Create Date: 2026-05-26 00:00:02
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260526_000002"
down_revision: Union[str, None] = "20260526_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_payments_system_ref", "payments", type_="unique")
    op.create_unique_constraint("uq_payments_system_ref", "payments", ["system_payment_id", "from_system"])


def downgrade() -> None:
    op.drop_constraint("uq_payments_system_ref", "payments", type_="unique")
    op.create_unique_constraint("uq_payments_system_ref", "payments", ["system_payment_id"])
