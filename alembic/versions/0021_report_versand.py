"""Report-Versand: Abo der Zusammenfassung je Benutzer:in und Versandprotokoll

Revision ID: 0021
Revises: 0020
Erstellt: 2026-11-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("benutzer", sa.Column("zusammenfassung_abo", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "report_versand",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("art", sa.String(20), nullable=False),  # fahrer | zusammenfassung
        sa.Column("zeitraum_art", sa.String(10), nullable=False),
        sa.Column("zeitraum_von", sa.Date(), nullable=False),
        sa.Column("zeitraum_bis", sa.Date(), nullable=False),
        sa.Column("zeitraum_label", sa.String(40), nullable=False),
        sa.Column("fahrer_id", sa.Integer(), sa.ForeignKey("fahrer.id", ondelete="SET NULL")),
        sa.Column("benutzer_id", sa.Integer(), sa.ForeignKey("benutzer.id", ondelete="SET NULL")),
        sa.Column("empfaenger_name", sa.String(200), nullable=False),
        sa.Column("an", sa.String(254), nullable=False),
        sa.Column("gesendet_am", sa.DateTime(), nullable=False),
        sa.Column("erfolg", sa.Boolean(), nullable=False),
        sa.Column("fehler", sa.Text()),
        sa.Column("ausgeloest", sa.String(12), nullable=False),  # manuell | automatisch
        sa.Column("ausgeloest_von", sa.Integer(), sa.ForeignKey("benutzer.id", ondelete="SET NULL")),
        sa.CheckConstraint("art IN ('fahrer', 'zusammenfassung')", name="ck_report_versand_art"),
        sa.CheckConstraint("ausgeloest IN ('manuell', 'automatisch')", name="ck_report_versand_ausgeloest"),
    )
    op.create_index("ix_report_versand_zeitraum", "report_versand", ["art", "zeitraum_von", "zeitraum_bis"])


def downgrade() -> None:
    op.drop_index("ix_report_versand_zeitraum", "report_versand")
    op.drop_table("report_versand")
    op.drop_column("benutzer", "zusammenfassung_abo")
