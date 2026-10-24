"""Order stripe checkout id

Revision ID: a3c1e9f04b27
Revises: 053707572361
Create Date: 2026-10-24 19:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c1e9f04b27"
down_revision: str | Sequence[str] | None = "053707572361"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("orders", sa.Column("stripe_checkout_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("orders", "stripe_checkout_id")
