"""cached English translations of foreign-language notices

A new table rather than a column on `tenders`: the scoring columns there are
frozen, and a multi-kilobyte text field would be read by every list query for
the sake of one detail panel. Nothing here is authoritative - it is a cache, so
the down migration is not lossy in any way that matters.

The unique constraint on (tender_id, target_language) is the cache itself. It is
what makes translating a notice idempotent when two people press the button at
the same instant, in the database rather than in a check-then-write race.

Revision ID: a1c4e7b9d206
Revises: f4a2c9e8b117
Create Date: 2026-08-31 09:05:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c4e7b9d206"
down_revision = "f4a2c9e8b117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tender_translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tender_id", sa.Integer(), nullable=False),
        sa.Column("source_language", sa.String(length=8), nullable=False),
        sa.Column("target_language", sa.String(length=8), nullable=False, server_default="en"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tender_id", "target_language", name="uq_translation_tender_target"),
    )
    op.create_index("ix_tender_translations_tender_id", "tender_translations", ["tender_id"])


def downgrade() -> None:
    # Safe to drop: every row is reproducible by pressing Translate again.
    op.drop_index("ix_tender_translations_tender_id", table_name="tender_translations")
    op.drop_table("tender_translations")
