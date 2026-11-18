"""AC/DC-Vorgabe je MSP; Herkunft der Stromart je Ladevorgang (rechnung | vorgabe | manuell) statt stromart_manuell

Revision ID: 0014
Revises: 0013
Erstellt: 2026-11-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("msp", sa.Column("stromart_standard", sa.String(2), nullable=True))
    op.add_column("ladevorgang", sa.Column("stromart_quelle", sa.String(10), nullable=True))
    op.execute("UPDATE ladevorgang SET stromart_quelle = 'manuell' WHERE stromart_manuell")
    op.execute("UPDATE ladevorgang SET stromart_quelle = 'rechnung' WHERE NOT stromart_manuell AND stromart IS NOT NULL")
    op.drop_column("ladevorgang", "stromart_manuell")


def downgrade() -> None:
    op.add_column("ladevorgang", sa.Column("stromart_manuell", sa.Boolean, server_default="false", nullable=False))
    op.execute("UPDATE ladevorgang SET stromart_manuell = true WHERE stromart_quelle = 'manuell'")
    op.execute("UPDATE ladevorgang SET stromart = NULL WHERE stromart_quelle = 'vorgabe'")
    op.drop_column("ladevorgang", "stromart_quelle")
    op.drop_column("msp", "stromart_standard")
