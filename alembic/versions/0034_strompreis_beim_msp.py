"""Strompreis für Importe ohne Kostenangabe beim MSP (statt in den Einstellungen)

Revision ID: 0034
Revises: 0033
Erstellt: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("msp", sa.Column("strompreis", sa.Numeric(8, 4)))
    conn = op.get_bind()
    standard = conn.execute(sa.text("SELECT wert FROM einstellung WHERE schluessel = 'strompreis_vorbelegung'")).scalar()
    # Vorbelegung je MSP: zuletzt beim Import verwendeter Preis, sonst der bisherige Einstellungswert (Standard 0,30)
    conn.execute(sa.text("""
        UPDATE msp SET strompreis = COALESCE(
            (SELECT i.strompreis_netto_kwh FROM import i WHERE i.msp_id = msp.id AND i.strompreis_netto_kwh IS NOT NULL
             ORDER BY i.id DESC LIMIT 1),
            CASE WHEN msp.parser_key = 'hastobe_csv' THEN CAST(:standard AS numeric) END)
    """), {"standard": standard or "0.30"})
    conn.execute(sa.text("DELETE FROM einstellung WHERE schluessel = 'strompreis_vorbelegung'"))


def downgrade() -> None:
    op.drop_column("msp", "strompreis")
