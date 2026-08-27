"""workspace roster

The list of addresses permitted to hold an account. Additive: with no rows the
API behaves exactly as it did before, because registration still accepts a
single-use invite and still bootstraps the first administrator.

Revision ID: e3b7c1d5f204
Revises: d3f7a10c2b58
Create Date: 2026-08-27 08:10:00.000000

Originally written against c7e1a4b90f32 and re-parented onto d3f7a10c2b58 when
the reviewer-feedback branch landed first. Both had been authored against the
same parent, which left alembic with **two heads** - a state git merges without
complaint and `alembic upgrade head` refuses outright, so it would have surfaced
as a failed deploy rather than a failed merge.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e3b7c1d5f204'
down_revision = 'd3f7a10c2b58'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'roster',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=False),
        sa.Column('added_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('joined_user_id', sa.Integer(), nullable=True),
        sa.Column('joined_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['added_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['joined_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_roster_email', 'roster', ['email'], unique=True)


def downgrade() -> None:
    # The join token itself lives in app_settings and is left alone: dropping a
    # settings row here would also delete it for anyone who downgrades, runs for
    # a while, and upgrades again.
    op.drop_index('ix_roster_email', table_name='roster')
    op.drop_table('roster')
