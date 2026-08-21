"""slack notification ledger

Adds the delivery ledger that makes Slack digests idempotent: one row per
(tender, channel), so a retried or double-fired run cannot re-announce a tender.
Additive only - no existing table is touched.

Revision ID: bd5848f10bf5
Revises: 8a32d37f649c
Create Date: 2026-08-21 16:06:27.547126
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'bd5848f10bf5'
down_revision = '8a32d37f649c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('slack_notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tender_id', sa.Integer(), nullable=False),
    sa.Column('channel_label', sa.String(length=64), nullable=False),
    sa.Column('run_batch_id', sa.String(length=64), nullable=False),
    sa.Column('trigger', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('claimed_at', sa.DateTime(), nullable=False),
    sa.Column('posted_at', sa.DateTime(), nullable=True),
    sa.Column('response_code', sa.Integer(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('relevance_score_at_send', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tender_id', 'channel_label', name='uq_slack_notification_tender_channel')
    )
    with op.batch_alter_table('slack_notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_slack_notifications_run_batch_id'), ['run_batch_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_slack_notifications_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_slack_notifications_tender_id'), ['tender_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('slack_notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_slack_notifications_tender_id'))
        batch_op.drop_index(batch_op.f('ix_slack_notifications_status'))
        batch_op.drop_index(batch_op.f('ix_slack_notifications_run_batch_id'))

    op.drop_table('slack_notifications')
