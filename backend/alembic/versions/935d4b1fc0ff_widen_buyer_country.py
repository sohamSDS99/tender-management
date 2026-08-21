"""widen buyer country

buyer_country was varchar(8), which fits an ISO code but not the full country
names the World Bank and OCDS feeds actually emit ("Indonesia"). SQLite does not
enforce VARCHAR limits so this only failed on PostgreSQL, where affected notices
were dropped one at a time by store_tenders' per-record guard.

Revision ID: 935d4b1fc0ff
Revises: 653aa67ec5a2
Create Date: 2026-08-21 16:22:54.599511
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '935d4b1fc0ff'
down_revision = '653aa67ec5a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('tenders', schema=None) as batch_op:
        batch_op.alter_column('buyer_country',
               existing_type=sa.VARCHAR(length=8),
               type_=sa.String(length=64),
               existing_nullable=True)



def downgrade() -> None:
    # Narrowing is lossy by definition: the pre-widening column physically cannot
    # hold the full country names this fixes ("Indonesia" is 9 characters), and on
    # PostgreSQL the ALTER fails outright rather than truncating. Values are
    # shortened first so the downgrade completes, which re-introduces the defect
    # this revision fixed - prefer restoring a dump. See docs/RUNBOOK.md section 7.
    op.execute(
        "UPDATE tenders SET buyer_country = substr(buyer_country, 1, 8) "
        "WHERE buyer_country IS NOT NULL"
    )
    with op.batch_alter_table('tenders', schema=None) as batch_op:
        batch_op.alter_column('buyer_country',
               existing_type=sa.String(length=64),
               type_=sa.VARCHAR(length=8),
               existing_nullable=True)

