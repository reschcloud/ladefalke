"""Fahrzeug: Autobahn-Vignette und Streckenmaut FLEX (Österreich) mit Gültigkeit und Schalter je Fahrzeug

Revision ID: 0024
Revises: 0023
Erstellt: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fahrzeug", sa.Column("vignette_noetig", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("fahrzeug", sa.Column("vignette_ab", sa.Date()))
    op.add_column("fahrzeug", sa.Column("vignette_bis", sa.Date()))
    op.add_column("fahrzeug", sa.Column("streckenmaut_noetig", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("fahrzeug", sa.Column("streckenmaut_ab", sa.Date()))
    op.add_column("fahrzeug", sa.Column("streckenmaut_bis", sa.Date()))


def downgrade() -> None:
    for spalte in ("streckenmaut_bis", "streckenmaut_ab", "streckenmaut_noetig", "vignette_bis", "vignette_ab",
                   "vignette_noetig"):
        op.drop_column("fahrzeug", spalte)
