"""CPO-Vorgabe je MSP (auch ohne öffentliche Kennung, z. B. eigene Ladestationen); Herkunft des CPO je Ladevorgang

Revision ID: 0016
Revises: 0015
Erstellt: 2026-11-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("msp", sa.Column("cpo_standard", sa.String(60), nullable=True))
    op.alter_column("ladevorgang", "betreiber_id", type_=sa.String(60), existing_type=sa.String(10))
    op.add_column("ladevorgang", sa.Column("betreiber_quelle", sa.String(10), nullable=True))
    op.execute("UPDATE ladevorgang SET betreiber_quelle = 'rechnung' WHERE betreiber_id IS NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE ladevorgang SET betreiber_id = NULL WHERE betreiber_quelle = 'vorgabe'")
    op.drop_column("ladevorgang", "betreiber_quelle")
    op.alter_column("ladevorgang", "betreiber_id", type_=sa.String(10), existing_type=sa.String(60))
    op.drop_column("msp", "cpo_standard")
