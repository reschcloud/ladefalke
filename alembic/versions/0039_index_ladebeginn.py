"""Index auf ladevorgang.start: Stichtag ist fest der Ladebeginn (ADR 0008), alle Zeitraumfilter laufen darüber

Revision ID: 0039
Revises: 0038
Erstellt: 2026-11-26
"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_ladevorgang_start", "ladevorgang", ["start"])


def downgrade() -> None:
    op.drop_index("ix_ladevorgang_start", "ladevorgang")
