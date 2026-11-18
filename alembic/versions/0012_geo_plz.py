"""Postleitzahlen-Verzeichnis (GeoNames) für die Geokodierung der Ladeorte; Daten per Befehl (README)

Revision ID: 0012
Revises: 0011
Erstellt: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "geo_plz",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("land", sa.String(2), nullable=False),
        sa.Column("plz", sa.String(20), nullable=False),
        sa.Column("plz_norm", sa.String(20), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("name_norm", sa.String(180), nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("lon", sa.Numeric(9, 6), nullable=False),
    )
    op.create_index("ix_geo_plz_land_plz", "geo_plz", ["land", "plz_norm"])
    op.create_index("ix_geo_plz_land_name", "geo_plz", ["land", "name_norm"])


def downgrade() -> None:
    op.drop_table("geo_plz")
