"""alarms: one row per problem, one open row per problem (D43)

Additive: a new table and nothing else. The partial unique index is the guard
that two evaluators running at once cannot open - and so cannot email - the same
problem twice; both dialects get the same WHERE.

Revision ID: a7d3e9f1c402
Revises: a1c4e7b9d206
Create Date: 2026-11-12 13:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a7d3e9f1c402'
down_revision = 'a1c4e7b9d206'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'alarms',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('check_key', sa.String(length=64), nullable=False),
        sa.Column('subject', sa.String(length=128), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('opened_at', sa.DateTime(), nullable=False),
        sa.Column('emailed_at', sa.DateTime(), nullable=True),
        sa.Column('reminded_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('fixed_emailed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_alarms_check_key', 'alarms', ['check_key'])
    op.create_index('ix_alarms_opened_at', 'alarms', ['opened_at'])
    op.create_index('ix_alarms_resolved_at', 'alarms', ['resolved_at'])
    op.create_index(
        'uq_alarms_open_problem',
        'alarms',
        ['check_key', 'subject'],
        unique=True,
        sqlite_where=sa.text('resolved_at IS NULL'),
        postgresql_where=sa.text('resolved_at IS NULL'),
    )


def downgrade() -> None:
    # Lossy in reverse: the alarm history goes with the table.
    op.drop_index('uq_alarms_open_problem', table_name='alarms')
    op.drop_index('ix_alarms_resolved_at', table_name='alarms')
    op.drop_index('ix_alarms_opened_at', table_name='alarms')
    op.drop_index('ix_alarms_check_key', table_name='alarms')
    op.drop_table('alarms')
