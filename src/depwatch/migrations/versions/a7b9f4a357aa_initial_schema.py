"""initial schema

The schema as it stood when migrations were introduced: packages, advisories,
ingest_runs and quarantine. Generated from the models, then read through -
`create_all()` had been the only way to build the schema until here, which
meant the first change against a real database would have been a manual one.

Revision ID: a7b9f4a357aa
Revises: 
Create Date: 2026-11-02 14:47:56.809512
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7b9f4a357aa'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('advisories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ecosystem', sa.String(length=32), nullable=False),
    sa.Column('package_name', sa.String(length=255), nullable=False),
    sa.Column('osv_id', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=True),
    sa.Column('cvss_vector', sa.String(length=128), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('aliases', sa.JSON(), nullable=True),
    sa.Column('fixed_version', sa.String(length=64), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ecosystem', 'package_name', 'osv_id', name='uq_advisory')
    )
    op.create_table('ingest_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requested', sa.Integer(), nullable=False),
    sa.Column('succeeded', sa.Integer(), nullable=False),
    sa.Column('quarantined', sa.Integer(), nullable=False),
    sa.Column('failed', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('packages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ecosystem', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('latest_version', sa.String(length=64), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('license', sa.String(length=128), nullable=True),
    sa.Column('repository_url', sa.Text(), nullable=True),
    sa.Column('maintainers', sa.Integer(), nullable=True),
    sa.Column('deprecated', sa.Boolean(), nullable=False),
    sa.Column('last_published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('known_versions', sa.JSON(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ecosystem', 'name', name='uq_packages_eco_name')
    )
    op.create_table('quarantine',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('key', sa.String(length=255), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('quarantine')
    op.drop_table('packages')
    op.drop_table('ingest_runs')
    op.drop_table('advisories')
