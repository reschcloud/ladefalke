"""Import zurückstellen: nicht lesbare Rechnungen auf eine Merkliste (später z. B. als Pauschale erfassen)

Revision ID: 0038
Revises: 0037
Erstellt: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE import_status ADD VALUE IF NOT EXISTS 'zurueckgestellt'")
    op.add_column("import", sa.Column("notiz", sa.String(300)))
    op.add_column("import", sa.Column("zurueckgestellt_am", sa.DateTime(timezone=True)))


def downgrade() -> None:
    # Ein Enum-Wert lässt sich in PostgreSQL nicht entfernen: zurückgestellte Importe werden wieder offene Vorschauen,
    # der Wert bleibt ungenutzt im Typ
    op.execute("UPDATE import SET status = 'vorschau' WHERE status = 'zurueckgestellt'")
    op.drop_column("import", "zurueckgestellt_am")
    op.drop_column("import", "notiz")
