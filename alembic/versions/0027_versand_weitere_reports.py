"""Versandprotokoll: auch „Karten und Preise je CPO“ und „Flottenbilanz“ (Versanddialog auf der Reports-Seite)

Revision ID: 0027
Revises: 0026
Erstellt: 2026-11-17
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

ALT = "art IN ('fahrer', 'zusammenfassung', 'aufgaben')"
NEU = "art IN ('fahrer', 'zusammenfassung', 'aufgaben', 'karten_cpo', 'flottenbilanz')"


def upgrade() -> None:
    op.drop_constraint("ck_report_versand_art", "report_versand", type_="check")
    op.create_check_constraint("ck_report_versand_art", "report_versand", NEU)


def downgrade() -> None:
    op.execute("DELETE FROM report_versand WHERE art IN ('karten_cpo', 'flottenbilanz')")
    op.drop_constraint("ck_report_versand_art", "report_versand", type_="check")
    op.create_check_constraint("ck_report_versand_art", "report_versand", ALT)
