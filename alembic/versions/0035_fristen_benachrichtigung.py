"""Benachrichtigung bei Fristen: Abo je Benutzer:in, gesendete Meldungen, Versandart „fristen“

Revision ID: 0035
Revises: 0034
Erstellt: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

ALT = "art IN ('fahrer', 'zusammenfassung', 'aufgaben', 'karten_cpo', 'flottenbilanz', 'zuordnungen')"
NEU = "art IN ('fahrer', 'zusammenfassung', 'aufgaben', 'karten_cpo', 'flottenbilanz', 'zuordnungen', 'fristen')"


def upgrade() -> None:
    op.add_column("benutzer", sa.Column("fristen_abo", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "frist_meldung",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("benutzer_id", sa.Integer(), sa.ForeignKey("benutzer.id", ondelete="CASCADE"), nullable=False),
        sa.Column("art", sa.String(20), nullable=False),
        sa.Column("objekt", sa.String(100), nullable=False),
        sa.Column("faellig", sa.Date()),
        sa.Column("stufe", sa.String(10), nullable=False),
        sa.Column("gesendet_am", sa.DateTime(), nullable=False))
    op.create_index("ix_frist_meldung_benutzer_id", "frist_meldung", ["benutzer_id"])
    op.drop_constraint("ck_report_versand_art", "report_versand", type_="check")
    op.create_check_constraint("ck_report_versand_art", "report_versand", NEU)


def downgrade() -> None:
    op.execute("DELETE FROM report_versand WHERE art = 'fristen'")
    op.drop_constraint("ck_report_versand_art", "report_versand", type_="check")
    op.create_check_constraint("ck_report_versand_art", "report_versand", ALT)
    op.drop_table("frist_meldung")
    op.drop_column("benutzer", "fristen_abo")
