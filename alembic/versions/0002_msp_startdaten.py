"""Startdaten: bekannte MSP

Revision ID: 0002
Revises: 0001
Erstellt: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

MSP = [
    ("Eigene Wallboxen (has.to.be)", "standort_csv", "privat"),
    ("Wien Energie", "wien_energie", "oeffentlich"),
    ("DCS / BMW Charging", "dcs", "oeffentlich"),
    ("LINZ AG", "linz_ag", "oeffentlich"),
    ("Salzburg AG", "salzburg_ag", "oeffentlich"),
    ("EnBW mobility+", "enbw", "oeffentlich"),
]


def upgrade() -> None:
    ladeart = postgresql.ENUM("oeffentlich", "privat", name="ladeart", create_type=False)
    msp = sa.table("msp", sa.column("name"), sa.column("parser_key"), sa.column("standard_ladeart", ladeart))
    op.bulk_insert(msp, [{"name": n, "parser_key": k, "standard_ladeart": a} for n, k, a in MSP])


def downgrade() -> None:
    keys = ", ".join(f"'{k}'" for _, k, _ in MSP)
    op.execute(f"DELETE FROM msp WHERE parser_key IN ({keys})")
