"""Tatsächliche Kosten: USt. je Ladevorgang und Gebühr, Steuersätze je Land, Vorsteuerabzug je Fahrzeug

Revision ID: 0031
Revises: 0030
Erstellt: 2026-11-23
"""
from datetime import date

from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

# Normalsätze (Prozent) ab Datum – Vorbelegung, in den Einstellungen änderbar (bitte prüfen)
SAETZE = [
    ("AT", "2020-01-01", "20"), ("DE", "2020-01-01", "19"), ("DE", "2020-07-01", "16"), ("DE", "2021-01-01", "19"),
    ("CH", "2020-01-01", "7.7"), ("CH", "2024-01-01", "8.1"), ("LI", "2020-01-01", "7.7"), ("LI", "2024-01-01", "8.1"),
    ("IT", "2020-01-01", "22"), ("SI", "2020-01-01", "22"), ("HU", "2020-01-01", "27"), ("CZ", "2020-01-01", "21"),
    ("SK", "2020-01-01", "20"), ("SK", "2025-01-01", "23"), ("HR", "2020-01-01", "25"), ("FR", "2020-01-01", "20"),
    ("NL", "2020-01-01", "21"), ("BE", "2020-01-01", "21"), ("LU", "2020-01-01", "17"), ("LU", "2023-01-01", "16"),
    ("LU", "2024-01-01", "17"), ("PL", "2020-01-01", "23"), ("DK", "2020-01-01", "25"), ("SE", "2020-01-01", "25"),
    ("ES", "2020-01-01", "21"), ("PT", "2020-01-01", "23"), ("FI", "2020-01-01", "24"), ("FI", "2024-09-01", "25.5"),
    ("NO", "2020-01-01", "25"), ("LT", "2020-01-01", "21"), ("LV", "2020-01-01", "21"), ("EE", "2020-01-01", "20"),
    ("EE", "2024-01-01", "22"), ("EE", "2025-07-01", "24"), ("RO", "2020-01-01", "19"), ("RO", "2025-08-01", "21"),
]


def upgrade() -> None:
    op.add_column("fahrzeug", sa.Column("vorsteuer_prozent", sa.Numeric(5, 2), nullable=False, server_default="100"))
    for tabelle in ("ladevorgang", "gebuehr"):
        op.add_column(tabelle, sa.Column("ust_betrag", sa.Numeric(12, 2)))
        op.add_column(tabelle, sa.Column("ust_satz", sa.Numeric(5, 2)))
        op.add_column(tabelle, sa.Column("ust_land", sa.String(2)))
    op.add_column("ladevorgang", sa.Column("kosten", sa.Numeric(12, 2)))  # berechnet die App beim Start
    tabelle = op.create_table(
        "steuersatz",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("land", sa.String(2), nullable=False),
        sa.Column("gueltig_ab", sa.Date(), nullable=False),
        sa.Column("satz", sa.Numeric(5, 2), nullable=False),
        sa.UniqueConstraint("land", "gueltig_ab", name="ux_steuersatz_land_ab"))
    op.bulk_insert(tabelle, [{"land": l, "gueltig_ab": date.fromisoformat(ab), "satz": s} for l, ab, s in SAETZE])


def downgrade() -> None:
    op.drop_table("steuersatz")
    op.drop_column("ladevorgang", "kosten")
    for tabelle in ("ladevorgang", "gebuehr"):
        for spalte in ("ust_land", "ust_satz", "ust_betrag"):
            op.drop_column(tabelle, spalte)
    op.drop_column("fahrzeug", "vorsteuer_prozent")
