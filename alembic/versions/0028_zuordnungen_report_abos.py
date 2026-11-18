"""Report „Zuordnungen am Stichtag“; Abos für Karten und Preise je CPO, Flottenbilanz und Zuordnungen am Stichtag

Revision ID: 0028
Revises: 0027
Erstellt: 2026-11-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

ABOS = ("karten_cpo_abo", "flottenbilanz_abo", "zuordnungen_abo")
ALT = "art IN ('fahrer', 'zusammenfassung', 'aufgaben', 'karten_cpo', 'flottenbilanz')"
NEU = "art IN ('fahrer', 'zusammenfassung', 'aufgaben', 'karten_cpo', 'flottenbilanz', 'zuordnungen')"


def upgrade() -> None:
    for spalte in ABOS:
        op.add_column("benutzer", sa.Column(spalte, sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_constraint("ck_report_versand_art", "report_versand", type_="check")
    op.create_check_constraint("ck_report_versand_art", "report_versand", NEU)


def downgrade() -> None:
    op.execute("DELETE FROM report_versand WHERE art = 'zuordnungen'")
    op.drop_constraint("ck_report_versand_art", "report_versand", type_="check")
    op.create_check_constraint("ck_report_versand_art", "report_versand", ALT)
    for spalte in ABOS:
        op.drop_column("benutzer", spalte)
