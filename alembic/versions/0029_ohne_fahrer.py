"""Zeiträume „ohne Fahrer:in“ je Fahrzeug; aneinandergrenzende Zuordnungen zusammenfassen

Revision ID: 0029
Revises: 0028
Erstellt: 2026-11-18
"""
from datetime import timedelta

from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _zusammenfassen(conn, tabelle: str, schluessel: str) -> None:
    """Perioden mit gleichem Schlüssel, die direkt aneinandergrenzen (Ende + 1 Tag = Beginn), zu einer zusammenfassen:
    die frühere wird verlängert, die spätere gelöscht (Überlappungen verhindert die Datenbank ohnehin)."""
    zeilen = conn.execute(sa.text(
        f"SELECT id, {schluessel}, gueltig_ab, gueltig_bis FROM {tabelle} ORDER BY {schluessel}, gueltig_ab")).all()
    vorige = None  # [id, schlüssel, ab, bis]
    for id_, *rest in zeilen:
        *schl, ab, bis = rest
        if (vorige and vorige[1] == schl and vorige[3] is not None
                and ab == vorige[3] + timedelta(days=1)):
            conn.execute(sa.text(f"DELETE FROM {tabelle} WHERE id = :i"), {"i": id_})
            conn.execute(sa.text(f"UPDATE {tabelle} SET gueltig_bis = :b WHERE id = :i"), {"b": bis, "i": vorige[0]})
            vorige[3] = bis
        else:
            vorige = [id_, schl, ab, bis]


def upgrade() -> None:
    op.create_table(
        "fahrzeug_ohne_fahrer",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fahrzeug_id", sa.Integer, nullable=False),
        sa.Column("gueltig_ab", sa.Date, nullable=False),
        sa.Column("gueltig_bis", sa.Date),
        sa.PrimaryKeyConstraint("id", name="pk_fahrzeug_ohne_fahrer"),
        sa.ForeignKeyConstraint(["fahrzeug_id"], ["fahrzeug.id"],
                                name="fk_fahrzeug_ohne_fahrer_fahrzeug_id_fahrzeug", ondelete="CASCADE"),
        sa.CheckConstraint("gueltig_bis IS NULL OR gueltig_bis >= gueltig_ab", name="ck_fahrzeug_ohne_fahrer_zeitraum"),
    )
    op.create_index("ix_fahrzeug_ohne_fahrer_fahrzeug_id", "fahrzeug_ohne_fahrer", ["fahrzeug_id"])
    op.execute(
        "ALTER TABLE fahrzeug_ohne_fahrer ADD CONSTRAINT ex_fahrzeug_ohne_fahrer_ueberlappung "
        "EXCLUDE USING gist (fahrzeug_id WITH =, daterange(gueltig_ab, gueltig_bis, '[]') WITH &&)"
    )
    conn = op.get_bind()
    _zusammenfassen(conn, "zuordnung_fahrer_fahrzeug", "fahrer_id, fahrzeug_id")
    _zusammenfassen(conn, "zuordnung_karte", "ladekarte_id, fahrer_id, fahrzeug_id")


def downgrade() -> None:
    op.drop_table("fahrzeug_ohne_fahrer")  # zusammengefasste Zuordnungen bleiben zusammengefasst
