"""Report „Offene Aufgaben“ per Mail: Abo je Benutzer:in und eigene Art im Versandprotokoll

Revision ID: 0025
Revises: 0024
Erstellt: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("benutzer", sa.Column("aufgaben_abo", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_constraint("ck_report_versand_art", "report_versand", type_="check")
    op.create_check_constraint("ck_report_versand_art", "report_versand", "art IN ('fahrer', 'zusammenfassung', 'aufgaben')")


def downgrade() -> None:
    op.execute("DELETE FROM report_versand WHERE art = 'aufgaben'")
    op.drop_constraint("ck_report_versand_art", "report_versand", type_="check")
    op.create_check_constraint("ck_report_versand_art", "report_versand", "art IN ('fahrer', 'zusammenfassung')")
    op.drop_column("benutzer", "aufgaben_abo")
