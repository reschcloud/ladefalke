"""Stammdaten: MSP, Ladekarten, Fahrer:innen, Fahrzeuge, Zuordnungen

Revision ID: 0001
Revises:
Erstellt: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ladeart = postgresql.ENUM("oeffentlich", "privat", name="ladeart", create_type=False)
kennung_typ = postgresql.ENUM("vertragsnummer", "kartennummer", "kartenlabel", name="kennung_typ",
                              create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    ladeart.create(op.get_bind(), checkfirst=True)
    kennung_typ.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "msp",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("parser_key", sa.String(50)),
        sa.Column("standard_ladeart", ladeart, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_msp"),
        sa.UniqueConstraint("name", name="uq_msp_name"),
        sa.UniqueConstraint("parser_key", name="uq_msp_parser_key"),
    )
    op.create_table(
        "fahrer",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("aktiv", sa.Boolean, nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id", name="pk_fahrer"),
    )
    op.create_table(
        "fahrzeug",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kennzeichen", sa.String(30), nullable=False),
        sa.Column("modell", sa.String(200)),
        sa.Column("aktiv", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("notiz", sa.Text),
        sa.PrimaryKeyConstraint("id", name="pk_fahrzeug"),
        sa.UniqueConstraint("kennzeichen", name="uq_fahrzeug_kennzeichen"),
    )
    op.create_table(
        "ladekarte",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("msp_id", sa.Integer, nullable=False),
        sa.Column("kennung", sa.String(200), nullable=False),
        sa.Column("kennung_typ", kennung_typ, nullable=False),
        sa.Column("bezeichnung", sa.String(200)),
        sa.Column("ist_gastkarte", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("aktiv", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("notiz", sa.Text),
        sa.PrimaryKeyConstraint("id", name="pk_ladekarte"),
        sa.ForeignKeyConstraint(["msp_id"], ["msp.id"], name="fk_ladekarte_msp_id_msp", ondelete="RESTRICT"),
        sa.UniqueConstraint("msp_id", "kennung", name="uq_ladekarte_msp_kennung"),
    )
    op.create_table(
        "zuordnung_karte",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ladekarte_id", sa.Integer, nullable=False),
        sa.Column("fahrer_id", sa.Integer),
        sa.Column("fahrzeug_id", sa.Integer),
        sa.Column("gueltig_ab", sa.Date, nullable=False),
        sa.Column("gueltig_bis", sa.Date),
        sa.PrimaryKeyConstraint("id", name="pk_zuordnung_karte"),
        sa.ForeignKeyConstraint(["ladekarte_id"], ["ladekarte.id"],
                                name="fk_zuordnung_karte_ladekarte_id_ladekarte", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fahrer_id"], ["fahrer.id"],
                                name="fk_zuordnung_karte_fahrer_id_fahrer", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fahrzeug_id"], ["fahrzeug.id"],
                                name="fk_zuordnung_karte_fahrzeug_id_fahrzeug", ondelete="RESTRICT"),
        sa.CheckConstraint("num_nonnulls(fahrer_id, fahrzeug_id) = 1",
                           name="ck_zuordnung_karte_genau_ein_ziel"),
        sa.CheckConstraint("gueltig_bis IS NULL OR gueltig_bis >= gueltig_ab",
                           name="ck_zuordnung_karte_zeitraum"),
    )
    op.create_index("ix_zuordnung_karte_ladekarte_id", "zuordnung_karte", ["ladekarte_id"])
    op.execute(
        "ALTER TABLE zuordnung_karte ADD CONSTRAINT ex_zuordnung_karte_ueberlappung "
        "EXCLUDE USING gist (ladekarte_id WITH =, daterange(gueltig_ab, gueltig_bis, '[]') WITH &&)"
    )

    op.create_table(
        "zuordnung_fahrer_fahrzeug",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fahrer_id", sa.Integer, nullable=False),
        sa.Column("fahrzeug_id", sa.Integer, nullable=False),
        sa.Column("gueltig_ab", sa.Date, nullable=False),
        sa.Column("gueltig_bis", sa.Date),
        sa.PrimaryKeyConstraint("id", name="pk_zuordnung_fahrer_fahrzeug"),
        sa.ForeignKeyConstraint(["fahrer_id"], ["fahrer.id"],
                                name="fk_zuordnung_fahrer_fahrzeug_fahrer_id_fahrer", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fahrzeug_id"], ["fahrzeug.id"],
                                name="fk_zuordnung_fahrer_fahrzeug_fahrzeug_id_fahrzeug", ondelete="RESTRICT"),
        sa.CheckConstraint("gueltig_bis IS NULL OR gueltig_bis >= gueltig_ab",
                           name="ck_zuordnung_fahrer_fahrzeug_zeitraum"),
    )
    op.create_index("ix_zuordnung_fahrer_fahrzeug_fahrer_id", "zuordnung_fahrer_fahrzeug", ["fahrer_id"])
    op.create_index("ix_zuordnung_fahrer_fahrzeug_fahrzeug_id", "zuordnung_fahrer_fahrzeug", ["fahrzeug_id"])
    # Je Fahrer:in keine Überlappung; ein Fahrzeug darf mehrere Fahrer:innen gleichzeitig haben (Pool)
    op.execute(
        "ALTER TABLE zuordnung_fahrer_fahrzeug ADD CONSTRAINT ex_zuordnung_fahrer_fahrzeug_ueberlappung "
        "EXCLUDE USING gist (fahrer_id WITH =, daterange(gueltig_ab, gueltig_bis, '[]') WITH &&)"
    )


def downgrade() -> None:
    op.drop_table("zuordnung_fahrer_fahrzeug")
    op.drop_table("zuordnung_karte")
    op.drop_table("ladekarte")
    op.drop_table("fahrzeug")
    op.drop_table("fahrer")
    op.drop_table("msp")
    kennung_typ.drop(op.get_bind(), checkfirst=True)
    ladeart.drop(op.get_bind(), checkfirst=True)
