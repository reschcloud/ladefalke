"""Schätzwerte je Fahrzeug (Ø DC-Leistung, Ø Ladeenergie) und Pauschalen

Revision ID: 0004
Revises: 0003
Erstellt: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ladeart = postgresql.ENUM("oeffentlich", "privat", name="ladeart", create_type=False)


def upgrade() -> None:
    op.add_column("fahrzeug", sa.Column("dc_kw_schnitt", sa.Numeric(6, 1), server_default="90", nullable=False))
    op.add_column("fahrzeug", sa.Column("ladeenergie_kwh_schnitt", sa.Numeric(6, 1), server_default="50",
                                        nullable=False))
    op.create_table(
        "pauschale",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("monat", sa.Date, nullable=False),
        sa.Column("bezeichnung", sa.String(200), nullable=False),
        sa.Column("kwh", sa.Numeric(10, 3), nullable=False),
        sa.Column("betrag_netto", sa.Numeric(12, 2), nullable=False),
        sa.Column("ladeart", ladeart, nullable=False),
        sa.Column("notiz", sa.Text),
        sa.PrimaryKeyConstraint("id", name="pk_pauschale"),
        sa.UniqueConstraint("monat", "bezeichnung", name="uq_pauschale_monat_bezeichnung"),
    )
    op.create_index("ix_pauschale_monat", "pauschale", ["monat"])


def downgrade() -> None:
    op.drop_table("pauschale")
    op.drop_column("fahrzeug", "ladeenergie_kwh_schnitt")
    op.drop_column("fahrzeug", "dc_kw_schnitt")
