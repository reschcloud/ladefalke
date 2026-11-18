"""Menüleiste nur noch links: Einstellung je Benutzer:in entfällt

Revision ID: 0026
Revises: 0025
Erstellt: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_benutzer_menue", "benutzer", type_="check")
    op.drop_column("benutzer", "menue")


def downgrade() -> None:
    op.add_column("benutzer", sa.Column("menue", sa.String(5), nullable=False, server_default="links"))
    op.create_check_constraint("ck_benutzer_menue", "benutzer", "menue IN ('oben', 'links')")
