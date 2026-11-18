"""Leasing-Beginn und -Ende je Fahrzeug, letzte Führerscheinkontrolle je Fahrer:in

Revision ID: 0017
Revises: 0016
Erstellt: 2026-11-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fahrzeug", sa.Column("leasing_beginn", sa.Date, nullable=True))
    op.add_column("fahrzeug", sa.Column("leasing_ende", sa.Date, nullable=True))
    op.add_column("fahrer", sa.Column("fuehrerschein_kontrolle", sa.Date, nullable=True))


def downgrade() -> None:
    op.drop_column("fahrer", "fuehrerschein_kontrolle")
    op.drop_column("fahrzeug", "leasing_ende")
    op.drop_column("fahrzeug", "leasing_beginn")
