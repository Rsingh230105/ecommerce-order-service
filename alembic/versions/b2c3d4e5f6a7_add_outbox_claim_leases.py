"""add outbox claim leases

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add crash-recoverable publisher claim fields."""
    op.add_column(
        "outbox_events",
        sa.Column("claim_token", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("claimed_until", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_outbox_events_claim_token",
        "outbox_events",
        ["claim_token"],
        unique=True,
    )
    op.create_index(
        "ix_outbox_events_claimed_until",
        "outbox_events",
        ["claimed_until"],
        unique=False,
    )


def downgrade() -> None:
    """Remove publisher claim fields."""
    op.drop_index(
        "ix_outbox_events_claimed_until",
        table_name="outbox_events",
    )
    op.drop_index(
        "ix_outbox_events_claim_token",
        table_name="outbox_events",
    )
    op.drop_column("outbox_events", "claimed_until")
    op.drop_column("outbox_events", "claim_token")