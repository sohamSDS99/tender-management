"""reviewer feedback, and the learned prediction it produces

Two separate things, deliberately in separate places (D26):

* ``tender_feedback`` holds what a person decided. It is keyed on the tender, so
  re-marking is an update and one notice can never hold two verdicts. Nothing in
  the fetch path writes here, which is what makes a verdict survive a re-score,
  a re-ingest and a content-hash change.
* two columns on ``tenders`` hold what the learner concluded from those
  verdicts. Derived, disposable and recomputed on every re-score - the flag is
  never the record of a human decision, only of a machine's guess at one.

Additive. With no rows in the new table the learner is inactive, every notice
carries ``auto_irrelevant = false`` and the API answers exactly as it did.

Downgrade drops the verdicts, which is the honest reverse of "this table did not
exist"; the two columns are pure derivation and lose nothing.

Revision ID: d3f7a10c2b58
Revises: c7e1a4b90f32
Create Date: 2026-08-27 09:40:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd3f7a10c2b58'
down_revision = 'c7e1a4b90f32'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'tender_feedback',
        sa.Column('tender_id', sa.Integer(), nullable=False),
        sa.Column('verdict', sa.String(length=16), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('tender_id'),
    )
    op.create_index('ix_tender_feedback_verdict', 'tender_feedback', ['verdict'])

    # server_default carries the existing rows: without it the NOT NULL column
    # cannot be added to a populated table, and every stored notice would need a
    # backfill statement to say the one thing the default already says.
    with op.batch_alter_table('tenders', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('auto_irrelevant', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column('auto_irrelevant_reasons', sa.JSON(), nullable=True))
    op.create_index('ix_tenders_auto_irrelevant', 'tenders', ['auto_irrelevant'])


def downgrade() -> None:
    op.drop_index('ix_tenders_auto_irrelevant', table_name='tenders')
    with op.batch_alter_table('tenders', schema=None) as batch_op:
        batch_op.drop_column('auto_irrelevant_reasons')
        batch_op.drop_column('auto_irrelevant')
    op.drop_index('ix_tender_feedback_verdict', table_name='tender_feedback')
    op.drop_table('tender_feedback')
