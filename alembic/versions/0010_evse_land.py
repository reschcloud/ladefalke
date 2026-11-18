"""Ladevorgang: normierte EVSE-ID, Betreiber-ID und Land; Bestand aus Ladepunkt und Rechnungsangaben befüllt

Revision ID: 0010
Revises: 0009
Erstellt: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa

from app.services.evse import ermitteln  # reine Funktion ohne Datenbank

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ladevorgang", sa.Column("evse_id", sa.String(60)))
    op.add_column("ladevorgang", sa.Column("betreiber_id", sa.String(10)))
    op.add_column("ladevorgang", sa.Column("land", sa.String(2)))
    op.create_index("ix_ladevorgang_land", "ladevorgang", ["land"])
    conn = op.get_bind()
    zeilen = conn.execute(sa.text(
        "SELECT l.id, m.parser_key, l.ladepunkt, l.rohdaten FROM ladevorgang l JOIN msp m ON m.id = l.msp_id")).all()
    for id_, parser_key, ladepunkt, rohdaten in zeilen:
        o = ermitteln(parser_key, ladepunkt, rohdaten)
        conn.execute(sa.text("UPDATE ladevorgang SET evse_id = :e, betreiber_id = :b, land = :l WHERE id = :i"),
                     {"e": o.evse_id, "b": o.betreiber_id, "l": o.land, "i": id_})


def downgrade() -> None:
    op.drop_index("ix_ladevorgang_land", "ladevorgang")
    op.drop_column("ladevorgang", "land")
    op.drop_column("ladevorgang", "betreiber_id")
    op.drop_column("ladevorgang", "evse_id")
