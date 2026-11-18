"""Lage der Menüleiste je Benutzer:in (links – Standard – oder oben)

Revision ID: 0023
Revises: 0022
Erstellt: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("benutzer", sa.Column("menue", sa.String(5), nullable=False, server_default="links"))
    op.create_check_constraint("ck_benutzer_menue", "benutzer", "menue IN ('oben', 'links')")


def downgrade() -> None:
    op.drop_constraint("ck_benutzer_menue", "benutzer", type_="check")
    op.drop_column("benutzer", "menue")
