"""Spritpreise mit „gültig ab“; Schalter für CO₂e-Einsparung und gesparte Tankkosten

Revision ID: 0032
Revises: 0031
Erstellt: 2026-11-23
"""
from datetime import date

from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tabelle = op.create_table(
        "spritpreis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gueltig_ab", sa.Date(), nullable=False, unique=True),
        sa.Column("preis", sa.Numeric(6, 3), nullable=False))
    conn = op.get_bind()
    werte = dict(conn.execute(sa.text("SELECT schluessel, wert FROM einstellung WHERE schluessel IN "
                                      "('spritpreis', 'co2e_strom')")).all())
    if werte.get("spritpreis"):  # bisheriger Einzelwert gilt ab 2020
        op.bulk_insert(tabelle, [{"gueltig_ab": date(2020, 1, 1), "preis": werte["spritpreis"]}])
    for schalter, quelle in (("feature_tank", "spritpreis"), ("feature_co2e", "co2e_strom")):
        if werte.get(quelle):
            conn.execute(sa.text("INSERT INTO einstellung (schluessel, wert) VALUES (:s, '1')"), {"s": schalter})
    conn.execute(sa.text("DELETE FROM einstellung WHERE schluessel = 'spritpreis'"))


def downgrade() -> None:
    conn = op.get_bind()
    preis = conn.execute(sa.text("SELECT preis FROM spritpreis ORDER BY gueltig_ab DESC LIMIT 1")).scalar()
    if preis is not None and conn.execute(sa.text("SELECT 1 FROM einstellung WHERE schluessel = 'feature_tank' "
                                                  "AND wert <> ''")).scalar():
        conn.execute(sa.text("INSERT INTO einstellung (schluessel, wert) VALUES ('spritpreis', :p)"), {"p": str(preis)})
    conn.execute(sa.text("DELETE FROM einstellung WHERE schluessel IN ('feature_tank', 'feature_co2e')"))
    op.drop_table("spritpreis")
