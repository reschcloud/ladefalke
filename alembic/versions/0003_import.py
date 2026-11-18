"""Import: Importe, Ladevorgänge, Gebühren; Vertragsnummer an Ladekarte

Revision ID: 0003
Revises: 0002
Erstellt: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ladeart = postgresql.ENUM("oeffentlich", "privat", name="ladeart", create_type=False)
import_status = postgresql.ENUM("vorschau", "abgeschlossen", name="import_status", create_type=False)
zuordnung_quelle = postgresql.ENUM("automatisch", "manuell", "keine", name="zuordnung_quelle", create_type=False)


def upgrade() -> None:
    import_status.create(op.get_bind(), checkfirst=True)
    zuordnung_quelle.create(op.get_bind(), checkfirst=True)

    op.add_column("ladekarte", sa.Column("vertragsnummer", sa.String(100)))
    op.create_index("ix_ladekarte_vertragsnummer", "ladekarte", ["vertragsnummer"])

    op.create_table(
        "import",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("dateiname", sa.String(300), nullable=False),
        sa.Column("datei_pfad", sa.String(300), nullable=False),
        sa.Column("datei_hash", sa.String(64), nullable=False),
        sa.Column("parser_key", sa.String(50)),
        sa.Column("msp_id", sa.Integer),
        sa.Column("ladeart", ladeart, nullable=False),
        sa.Column("strompreis_netto_kwh", sa.Numeric(8, 4)),
        sa.Column("rechnungsnummer", sa.String(100)),
        sa.Column("rechnungsdatum", sa.Date),
        sa.Column("abrechnungszeitraum_von", sa.Date),
        sa.Column("abrechnungszeitraum_bis", sa.Date),
        sa.Column("rechnungssumme_netto", sa.Numeric(12, 2)),
        sa.Column("hochgeladen_am", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("importiert_am", sa.DateTime(timezone=True)),
        sa.Column("status", import_status, nullable=False),
        sa.Column("warnungen", postgresql.JSONB, server_default="[]", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_import"),
        sa.ForeignKeyConstraint(["msp_id"], ["msp.id"], name="fk_import_msp_id_msp", ondelete="RESTRICT"),
    )
    op.create_index("uq_import_datei_hash_abgeschlossen", "import", ["datei_hash"], unique=True,
                    postgresql_where=sa.text("status = 'abgeschlossen'"))

    op.create_table(
        "ladevorgang",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("import_id", sa.Integer, nullable=False),
        sa.Column("msp_id", sa.Integer, nullable=False),
        sa.Column("externe_id", sa.String(100)),
        sa.Column("ladekarte_id", sa.Integer),
        sa.Column("roh_kartenkennung", sa.String(200)),
        sa.Column("start", sa.DateTime, nullable=False),
        sa.Column("ende", sa.DateTime, nullable=False),
        sa.Column("kwh", sa.Numeric(10, 3), nullable=False),
        sa.Column("kwh_geschaetzt", sa.Boolean, server_default="false", nullable=False),
        sa.Column("kosten_netto", sa.Numeric(12, 2), nullable=False),
        sa.Column("standort", sa.String(300)),
        sa.Column("ladepunkt", sa.String(100)),
        sa.Column("ladeart", ladeart, nullable=False),
        sa.Column("fahrzeug_id", sa.Integer),
        sa.Column("zuordnung_quelle", zuordnung_quelle, nullable=False),
        sa.Column("rohdaten", postgresql.JSONB, server_default="{}", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ladevorgang"),
        sa.ForeignKeyConstraint(["import_id"], ["import.id"], name="fk_ladevorgang_import_id_import",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["msp_id"], ["msp.id"], name="fk_ladevorgang_msp_id_msp", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ladekarte_id"], ["ladekarte.id"], name="fk_ladevorgang_ladekarte_id_ladekarte",
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fahrzeug_id"], ["fahrzeug.id"], name="fk_ladevorgang_fahrzeug_id_fahrzeug",
                                ondelete="RESTRICT"),
    )
    op.create_index("ix_ladevorgang_import_id", "ladevorgang", ["import_id"])
    op.create_index("ix_ladevorgang_ladekarte_id", "ladevorgang", ["ladekarte_id"])
    op.create_index("ix_ladevorgang_fahrzeug_id", "ladevorgang", ["fahrzeug_id"])
    op.create_index("ix_ladevorgang_ende", "ladevorgang", ["ende"])
    op.create_index("uq_ladevorgang_msp_externe_id", "ladevorgang", ["msp_id", "externe_id"], unique=True,
                    postgresql_where=sa.text("externe_id IS NOT NULL"))

    op.create_table(
        "gebuehr",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("import_id", sa.Integer, nullable=False),
        sa.Column("ladekarte_id", sa.Integer),
        sa.Column("roh_kartenkennung", sa.String(200)),
        sa.Column("zeitraum_von", sa.Date),
        sa.Column("zeitraum_bis", sa.Date),
        sa.Column("bezeichnung", sa.String(300), nullable=False),
        sa.Column("betrag_netto", sa.Numeric(12, 2), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_gebuehr"),
        sa.ForeignKeyConstraint(["import_id"], ["import.id"], name="fk_gebuehr_import_id_import", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ladekarte_id"], ["ladekarte.id"], name="fk_gebuehr_ladekarte_id_ladekarte",
                                ondelete="RESTRICT"),
    )
    op.create_index("ix_gebuehr_import_id", "gebuehr", ["import_id"])
    op.create_index("ix_gebuehr_ladekarte_id", "gebuehr", ["ladekarte_id"])


def downgrade() -> None:
    op.drop_table("gebuehr")
    op.drop_table("ladevorgang")
    op.drop_table("import")
    op.drop_index("ix_ladekarte_vertragsnummer", table_name="ladekarte")
    op.drop_column("ladekarte", "vertragsnummer")
    zuordnung_quelle.drop(op.get_bind(), checkfirst=True)
    import_status.drop(op.get_bind(), checkfirst=True)
