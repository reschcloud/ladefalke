"""Startdaten: MSP Elli / Cupra Charging

Revision ID: 0006
Revises: 0005
Erstellt: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ladeart = postgresql.ENUM("oeffentlich", "privat", name="ladeart", create_type=False)
    msp = sa.table("msp", sa.column("name"), sa.column("parser_key"), sa.column("standard_ladeart", ladeart))
    # nur anlegen, wenn nicht schon (z. B. manuell) vorhanden
    vorhanden = op.get_bind().execute(sa.text("SELECT 1 FROM msp WHERE parser_key = 'elli' OR name = 'Elli / Cupra Charging'"))
    if vorhanden.first() is None:
        op.bulk_insert(msp, [{"name": "Elli / Cupra Charging", "parser_key": "elli", "standard_ladeart": "oeffentlich"}])


def downgrade() -> None:
    op.execute("DELETE FROM msp WHERE parser_key = 'elli' AND id NOT IN (SELECT msp_id FROM ladekarte) "
               "AND id NOT IN (SELECT msp_id FROM import WHERE msp_id IS NOT NULL)")
