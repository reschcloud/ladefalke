"""Vorausgewählter Zeitraum je Benutzer:in (Mein Konto)

Revision ID: 0030
Revises: 0029
Erstellt: 2026-11-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("benutzer", sa.Column("zeitraum", sa.String(10), nullable=False, server_default="monat"))
    op.create_check_constraint("ck_benutzer_zeitraum", "benutzer",
                               "zeitraum IN ('monat', 'quartal', 'halbjahr', 'jahr', 'alles')")


def downgrade() -> None:
    op.drop_constraint("ck_benutzer_zeitraum", "benutzer", type_="check")
    op.drop_column("benutzer", "zeitraum")
