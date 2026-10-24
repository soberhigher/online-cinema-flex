"""empty baseline

Revision ID: fc98220386a8
Revises:
Create Date: 2026-10-24 14:04:22.485545

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "fc98220386a8"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
