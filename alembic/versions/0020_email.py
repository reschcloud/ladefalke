"""E-Mail-Adressen bei Fahrer:innen und Benutzer:innen (für den Mail-Versand)

Revision ID: 0020
Revises: 0019
Erstellt: 2026-11-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fahrer", sa.Column("email", sa.String(254)))
    op.add_column("benutzer", sa.Column("email", sa.String(254)))


def downgrade() -> None:
    op.drop_column("benutzer", "email")
    op.drop_column("fahrer", "email")
