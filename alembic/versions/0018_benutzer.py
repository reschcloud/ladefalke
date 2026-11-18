"""Benutzer mit Rollen (admin, operator, viewer) und eigenem Theme; das globale Theme entfällt

Revision ID: 0018
Revises: 0017
Erstellt: 2026-11-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benutzer",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("benutzername", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("rolle", sa.String(10), nullable=False),
        sa.Column("passwort_hash", sa.String(300), nullable=False),
        sa.Column("aktiv", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("theme", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("angelegt_am", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("letzte_anmeldung", sa.DateTime(timezone=True)),
        sa.CheckConstraint("rolle IN ('admin', 'operator', 'viewer')", name="ck_benutzer_rolle"),
        sa.CheckConstraint("theme IN ('auto', 'hell', 'dunkel')", name="ck_benutzer_theme"),
    )
    op.create_index("ux_benutzer_benutzername", "benutzer", [sa.text("lower(benutzername)")], unique=True)
    op.execute("DELETE FROM einstellung WHERE schluessel = 'theme'")  # Theme ist jetzt je Benutzer


def downgrade() -> None:
    op.drop_index("ux_benutzer_benutzername", table_name="benutzer")
    op.drop_table("benutzer")
