"""Migrations, and whether they still describe the models.

`init_db()` (create_all) is the fast path the tests use; migrations are the
only path production takes. Two ways to build a schema is a liability unless
something proves they agree, and that is what the drift test is for: add a
column to a model without writing a revision and this fails.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from depwatch.db import init_db
from depwatch.migrate import MIGRATIONS_DIR, downgrade_to, upgrade_to_head
from depwatch.models import Base

EXPECTED_TABLES = {"packages", "advisories", "ingest_runs", "quarantine"}


def test_upgrade_builds_the_whole_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    upgrade_to_head(url)

    tables = set(inspect(create_engine(url)).get_table_names())
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables  # the schema now has a version


def test_the_migrations_match_the_models(tmp_path):
    """The drift test. A model change without a revision fails here."""
    url = f"sqlite:///{tmp_path / 'drift.db'}"
    upgrade_to_head(url)

    engine = create_engine(url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        difference = compare_metadata(context, Base.metadata)

    assert difference == [], f"models and migrations disagree: {difference}"


def test_upgrading_twice_changes_nothing(tmp_path):
    """Boot runs this on every start, including starts that need no migration."""
    url = f"sqlite:///{tmp_path / 'twice.db'}"
    upgrade_to_head(url)
    upgrade_to_head(url)

    assert EXPECTED_TABLES <= set(inspect(create_engine(url)).get_table_names())


def test_downgrade_removes_what_upgrade_added(tmp_path):
    """A revision you cannot reverse is a revision you cannot deploy twice."""
    url = f"sqlite:///{tmp_path / 'down.db'}"
    upgrade_to_head(url)
    downgrade_to("base", url)

    remaining = set(inspect(create_engine(url)).get_table_names())
    assert not (EXPECTED_TABLES & remaining)


def test_create_all_and_migrations_produce_the_same_tables(tmp_path):
    """The two paths have to agree on more than just existing."""
    migrated = f"sqlite:///{tmp_path / 'a.db'}"
    upgrade_to_head(migrated)
    created = f"sqlite:///{tmp_path / 'b.db'}"
    init_db(created)

    def columns(url: str) -> dict[str, set[str]]:
        inspector = inspect(create_engine(url))
        return {
            table: {c["name"] for c in inspector.get_columns(table)}
            for table in sorted(EXPECTED_TABLES)
        }

    assert columns(migrated) == columns(created)


def test_the_revisions_live_inside_the_package(tmp_path):
    """They have to ship in the wheel, or `upgrade head` on the deployed
    instance finds no scripts and silently does nothing."""
    assert (MIGRATIONS_DIR / "env.py").is_file()
    revisions = list((MIGRATIONS_DIR / "versions").glob("*.py"))
    assert revisions, "no revision files found"
