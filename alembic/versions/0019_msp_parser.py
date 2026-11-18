"""MSP: Parser aktiv/deaktiviert und Abrechnung je Ladekarte (bisher fest im Code)

Revision ID: 0019
Revises: 0018
Erstellt: 2026-11-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("msp", sa.Column("parser_aktiv", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("msp", sa.Column("abrechnung_je_karte", sa.Boolean(), nullable=False, server_default=sa.false()))
    # bisher im Kern hinterlegt (vollstaendigkeit.JE_KARTE): dieser Anbieter rechnet je Ladekarte ab
    op.execute("UPDATE msp SET abrechnung_je_karte = true WHERE parser_key = 'dcs'")


def downgrade() -> None:
    op.drop_column("msp", "abrechnung_je_karte")
    op.drop_column("msp", "parser_aktiv")
