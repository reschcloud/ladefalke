"""CPO-Liste für AC/DC: Betreiber, die immer nur AC oder nur DC anbieten (z. B. Ionity = DC)

Revision ID: 0037
Revises: 0036
Erstellt: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cpo_stromart",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("kennungen", JSONB, nullable=False, server_default="[]"),
        sa.Column("stromart", sa.String(2), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_cpo_stromart"),
        sa.UniqueConstraint("name", name="uq_cpo_stromart_name"),
        sa.CheckConstraint("stromart IN ('AC', 'DC')", name="ck_cpo_stromart_stromart"),
    )


def downgrade() -> None:
    op.drop_table("cpo_stromart")
