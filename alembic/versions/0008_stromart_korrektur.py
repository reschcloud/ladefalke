"""Ladevorgang: Stromart manuell korrigiert, aus Ladeprofil ausgenommen

Revision ID: 0008
Revises: 0007
Erstellt: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ladevorgang", sa.Column("stromart_manuell", sa.Boolean, server_default="false", nullable=False))
    op.add_column("ladevorgang", sa.Column("profil_ausgenommen", sa.Boolean, server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("ladevorgang", "profil_ausgenommen")
    op.drop_column("ladevorgang", "stromart_manuell")
