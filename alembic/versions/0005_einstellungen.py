"""Einstellungen (Schlüssel/Wert)

Revision ID: 0005
Revises: 0004
Erstellt: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "einstellung",
        sa.Column("schluessel", sa.String(50), nullable=False),
        sa.Column("wert", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("schluessel", name="pk_einstellung"),
    )


def downgrade() -> None:
    op.drop_table("einstellung")
