"""Fahrzeug: manuelle Schätzwerte entfernt – Grundlage der kWh-Schätzung kommt aus den Ladedaten

Revision ID: 0009
Revises: 0008
Erstellt: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("fahrzeug", "ladeenergie_kwh_schnitt")
    op.drop_column("fahrzeug", "dc_kw_schnitt")


def downgrade() -> None:
    op.add_column("fahrzeug", sa.Column("dc_kw_schnitt", sa.Numeric(6, 1), server_default="90", nullable=False))
    op.add_column("fahrzeug", sa.Column("ladeenergie_kwh_schnitt", sa.Numeric(6, 1), server_default="50",
                                        nullable=False))
