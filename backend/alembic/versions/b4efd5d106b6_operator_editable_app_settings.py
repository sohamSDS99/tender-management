"""operator editable app settings

Holds settings a human changes from the UI - currently only the sweep hours -
so the value survives a container restart and is not whatever the image was
started with. Additive; the environment variable remains the fallback default.

Revision ID: b4efd5d106b6
Revises: 935d4b1fc0ff
Create Date: 2026-08-24 09:03:23.281674
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'b4efd5d106b6'
down_revision = '935d4b1fc0ff'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade() -> None:
    op.drop_table('app_settings')
