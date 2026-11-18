"""Ladeort: manuell eingetragener Ortsname (statt PLZ) und Zustand „verzichtet“ (bewusst ohne Verortung)

Revision ID: 0015
Revises: 0014
Erstellt: 2026-11-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ladeort", sa.Column("ort_manuell", sa.String(120), nullable=True))


def downgrade() -> None:
    op.execute("UPDATE ladeort SET genauigkeit = NULL WHERE genauigkeit = 'verzichtet'")
    op.drop_column("ladeort", "ort_manuell")
