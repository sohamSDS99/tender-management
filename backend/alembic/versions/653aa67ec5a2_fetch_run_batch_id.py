"""fetch run batch id

Groups the per-source FetchRun rows of one scheduled sweep under a single
batch id, so a run can be correlated with the slack_notifications it produced.
Additive nullable column - existing rows stay valid.

Revision ID: 653aa67ec5a2
Revises: bd5848f10bf5
Create Date: 2026-08-21 16:12:07.466431
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '653aa67ec5a2'
down_revision = 'bd5848f10bf5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('fetch_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('batch_id', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_fetch_runs_batch_id'), ['batch_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('fetch_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fetch_runs_batch_id'))
        batch_op.drop_column('batch_id')

