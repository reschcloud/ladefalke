"""Ladevorgang.stromart (AC/DC laut Rechnung), aus den Rohdaten nachgetragen

Revision ID: 0007
Revises: 0006
Erstellt: 2026-11-05
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _normieren(text):
    t = (text or "").strip().upper()
    return "AC" if t == "AC" else "DC" if t in ("DC", "HPC") else None


def _aus_rohdaten(parser_key, roh):
    """Wie die Parser: nur ausdrückliche Angaben der Rechnung (Stand der Rohdaten bis Revision 0006)."""
    roh = roh or {}
    if parser_key == "dcs":
        return _normieren(roh.get("produkt"))
    if parser_key == "linz_ag":
        return _normieren(roh.get("art"))
    if parser_key == "wien_energie":
        m = re.search(r",(AC|DC)\s?\d+(?:,\d+)?\s*-\s*Ladekarte", roh.get("kopfzeile") or "")
        return m.group(1) if m else None
    if parser_key == "enbw":
        zeilen = roh.get("zeilen") or []
        m = re.search(r"\s(AC|DC)\s", zeilen[0]) if zeilen else None
        return m.group(1) if m else None
    return None  # Standort-CSV, Salzburg AG, Elli: keine Angabe


def upgrade() -> None:
    op.add_column("ladevorgang", sa.Column("stromart", sa.String(2)))
    conn = op.get_bind()
    zeilen = conn.execute(sa.text(
        "SELECT l.id, m.parser_key, l.rohdaten FROM ladevorgang l JOIN msp m ON m.id = l.msp_id "
        "WHERE m.parser_key IN ('dcs', 'linz_ag', 'wien_energie', 'enbw')")).all()
    for lv_id, parser_key, roh in zeilen:
        if (art := _aus_rohdaten(parser_key, roh)) is not None:
            conn.execute(sa.text("UPDATE ladevorgang SET stromart = :a WHERE id = :i"), {"a": art, "i": lv_id})


def downgrade() -> None:
    op.drop_column("ladevorgang", "stromart")
