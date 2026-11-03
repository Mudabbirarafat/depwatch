"""packages: github enrichment

The registries carry weak maintenance signals - npm's deprecation flag is
rarely set and PyPI has none at all. An archived repository is a deliberate
statement that a project is over, and the last push beats the last release as
an activity signal. All nullable: 82% of real packages resolve to a GitHub
repository, and the rest are hosted elsewhere or declare none.

Revision ID: 1a5a79da7c0d
Revises: e700a15fc73a
Create Date: 2026-11-03 16:42:01.146070
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '1a5a79da7c0d'
down_revision: str | None = 'e700a15fc73a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('packages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('repo_full_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('repo_archived', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('repo_pushed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('repo_contributors', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('repo_stars', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('repo_open_issues', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('repo_checked_at', sa.DateTime(timezone=True), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('packages', schema=None) as batch_op:
        batch_op.drop_column('repo_checked_at')
        batch_op.drop_column('repo_open_issues')
        batch_op.drop_column('repo_stars')
        batch_op.drop_column('repo_contributors')
        batch_op.drop_column('repo_pushed_at')
        batch_op.drop_column('repo_archived')
        batch_op.drop_column('repo_full_name')

