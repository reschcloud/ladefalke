"""Spritpreis: Quelle (manuell bzw. EU Weekly Oil Bulletin)

Revision ID: 0033
Revises: 0032
Erstellt: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("spritpreis", sa.Column("quelle", sa.String(20), nullable=False, server_default="manuell"))


def downgrade() -> None:
    op.drop_column("spritpreis", "quelle")
