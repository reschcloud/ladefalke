"""Ladeorte: gleiche Adresse → ein Ladeort mit PLZ/Ort (Koordinaten folgen in Phase 3); Bestand zugeordnet

Revision ID: 0011
Revises: 0010
Erstellt: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa

from app.services.ladeorte import analysieren  # reine Funktion; die Zuordnung selbst hier per SQL

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ladeort",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("schluessel", sa.String(400), nullable=False, unique=True),
        sa.Column("land", sa.String(2)),
        sa.Column("adresse", sa.String(300), nullable=False),
        sa.Column("plz", sa.String(12)),
        sa.Column("ort", sa.String(120)),
        sa.Column("plz_manuell", sa.String(12)),
        sa.Column("lat", sa.Numeric(9, 6)),
        sa.Column("lon", sa.Numeric(9, 6)),
        sa.Column("genauigkeit", sa.String(10)),
    )
    op.add_column("ladevorgang", sa.Column("ladeort_id", sa.Integer,
                                           sa.ForeignKey("ladeort.id", ondelete="SET NULL",
                                                         name="fk_ladevorgang_ladeort_id_ladeort")))
    op.create_index("ix_ladevorgang_ladeort_id", "ladevorgang", ["ladeort_id"])

    conn = op.get_bind()
    orte: dict[str, int] = {}
    for id_, standort, land in conn.execute(sa.text("SELECT id, standort, land FROM ladevorgang")).all():
        a = analysieren(standort, land)
        if a is None:
            continue
        if a.schluessel not in orte:
            orte[a.schluessel] = conn.execute(sa.text(
                "INSERT INTO ladeort (schluessel, land, adresse, plz, ort) VALUES (:s, :l, :a, :p, :o) RETURNING id"),
                {"s": a.schluessel, "l": land, "a": a.text[:300], "p": a.plz, "o": (a.ort or "")[:120] or None}).scalar()
        conn.execute(sa.text("UPDATE ladevorgang SET ladeort_id = :o WHERE id = :i"), {"o": orte[a.schluessel], "i": id_})


def downgrade() -> None:
    op.drop_index("ix_ladevorgang_ladeort_id", "ladevorgang")
    op.drop_constraint("fk_ladevorgang_ladeort_id_ladeort", "ladevorgang", type_="foreignkey")
    op.drop_column("ladevorgang", "ladeort_id")
    op.drop_table("ladeort")
