"""Zeiträume „ohne Dienstwagen“ je Fahrer:in (mit Grund); Fahrzeuge als Ersatz-/Mietfahrzeug kennzeichnen

Revision ID: 0036
Revises: 0035
Erstellt: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fahrer_ohne_fahrzeug",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fahrer_id", sa.Integer, nullable=False),
        sa.Column("gueltig_ab", sa.Date, nullable=False),
        sa.Column("gueltig_bis", sa.Date),
        sa.Column("grund", sa.String(200)),
        sa.PrimaryKeyConstraint("id", name="pk_fahrer_ohne_fahrzeug"),
        sa.ForeignKeyConstraint(["fahrer_id"], ["fahrer.id"],
                                name="fk_fahrer_ohne_fahrzeug_fahrer_id_fahrer", ondelete="CASCADE"),
        sa.CheckConstraint("gueltig_bis IS NULL OR gueltig_bis >= gueltig_ab", name="ck_fahrer_ohne_fahrzeug_zeitraum"),
    )
    op.create_index("ix_fahrer_ohne_fahrzeug_fahrer_id", "fahrer_ohne_fahrzeug", ["fahrer_id"])
    op.execute(
        "ALTER TABLE fahrer_ohne_fahrzeug ADD CONSTRAINT ex_fahrer_ohne_fahrzeug_ueberlappung "
        "EXCLUDE USING gist (fahrer_id WITH =, daterange(gueltig_ab, gueltig_bis, '[]') WITH &&)"
    )
    op.add_column("fahrzeug", sa.Column("ersatzfahrzeug", sa.Boolean, nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("fahrzeug", "ersatzfahrzeug")
    op.drop_table("fahrer_ohne_fahrzeug")
