"""per-person access tokens on the roster

Each roster entry carries its own durable link, which is the credential: opening
it signs that person in, with no password. Additive and nullable, so existing
rows simply have no link until one is issued.

Revision ID: f4a2c9e8b117
Revises: e3b7c1d5f204
Create Date: 2026-08-27 09:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f4a2c9e8b117'
down_revision = 'e3b7c1d5f204'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('roster', sa.Column('access_token', sa.String(length=64), nullable=True))
    op.create_index('ix_roster_access_token', 'roster', ['access_token'], unique=True)


def downgrade() -> None:
    # Lossy in reverse, and it has to be: dropping the column discards every
    # issued link, so everyone who has not yet joined needs a new one. There is
    # nowhere else to keep them.
    op.drop_index('ix_roster_access_token', table_name='roster')
    op.drop_column('roster', 'access_token')
