"""stub to reconcile missing revision in production db

Revision ID: 20260424_000004
Revises: 20260424_000003
Create Date: 2026-04-24 00:00:04
"""

from typing import Sequence, Union

revision: str = "20260424_000004"
down_revision: Union[str, None] = "20260424_000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
