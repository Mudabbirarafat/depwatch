"""advisories: last_affected

Some OSV advisories bound themselves from above - `last_affected` - instead of
naming a fixed version. Reading only `fixed` recorded those as "never fixed",
so a 2012 advisory affecting Django up to 1.3.1 counted against Django 6.1.2
and inflated its score permanently. This column stores that bound.

Revision ID: c6699931b8a2
Revises: a7b9f4a357aa
Create Date: 2026-11-03 13:44:13.694415
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c6699931b8a2'
down_revision: str | None = 'a7b9f4a357aa'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('advisories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_affected', sa.String(length=64), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('advisories', schema=None) as batch_op:
        batch_op.drop_column('last_affected')

