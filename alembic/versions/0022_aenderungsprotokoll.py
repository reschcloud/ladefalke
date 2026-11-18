"""Änderungsprotokoll: wer hat was wann geändert (services/protokoll.py)

Revision ID: 0022
Revises: 0021
Erstellt: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aenderung",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("zeitpunkt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("benutzer_id", sa.Integer(), sa.ForeignKey("benutzer.id", ondelete="SET NULL")),
        sa.Column("benutzer_name", sa.String(200), nullable=False),  # Name zum Zeitpunkt bzw. „System“
        sa.Column("aktion", sa.String(30), nullable=False),
        sa.Column("objekt_typ", sa.String(30), nullable=False),
        sa.Column("objekt_id", sa.Integer()),
        sa.Column("bezeichnung", sa.String(300), nullable=False),
        sa.Column("felder", postgresql.JSONB(), nullable=False, server_default="{}"),  # Feld → [alt, neu]
        sa.Column("kontext", sa.String(300)),
    )
    op.create_index("ix_aenderung_zeitpunkt", "aenderung", ["zeitpunkt"])
    op.create_index("ix_aenderung_objekt", "aenderung", ["objekt_typ", "objekt_id"])


def downgrade() -> None:
    op.drop_index("ix_aenderung_objekt", "aenderung")
    op.drop_index("ix_aenderung_zeitpunkt", "aenderung")
    op.drop_table("aenderung")
