"""Importer „Standort-CSV“ heißt nach der Ladesoftware has.to.be: parser_key standort_csv → hastobe_csv

Revision ID: 0013
Revises: 0012
Erstellt: 2026-11-10
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for tabelle in ("msp", "import"):
        op.execute(f"UPDATE \"{tabelle}\" SET parser_key = 'hastobe_csv' WHERE parser_key = 'standort_csv'")


def downgrade() -> None:
    for tabelle in ("msp", "import"):
        op.execute(f"UPDATE \"{tabelle}\" SET parser_key = 'standort_csv' WHERE parser_key = 'hastobe_csv'")
