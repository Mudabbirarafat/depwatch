"""packages: materialised score

Risk is a Python function, so ordering by it could not be pushed into SQL and
/packages scored the whole ecosystem before returning a page of ten. These
columns store the score at each package's latest version, and the index is the
ordering that endpoint asks for - without it the work would only move from a
Python sort to a SQL filesort.

Revision ID: e700a15fc73a
Revises: c6699931b8a2
Create Date: 2026-11-03 13:48:17.906843
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e700a15fc73a'
down_revision: str | None = 'c6699931b8a2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('packages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('score', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('band', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('reasons', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('scored_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index('ix_packages_eco_score', ['ecosystem', 'score'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('packages', schema=None) as batch_op:
        batch_op.drop_index('ix_packages_eco_score')
        batch_op.drop_column('scored_at')
        batch_op.drop_column('reasons')
        batch_op.drop_column('band')
        batch_op.drop_column('score')

