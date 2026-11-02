"""Storage bootstrap.

The upsert behaviour is covered in test_ingest.py; what is tested here is
getting a usable database in the first place, which is where the deployed
instance differs from a development machine.
"""

from sqlalchemy import func, select, text

from depwatch.db import init_db, session_scope
from depwatch.models import Package


def test_init_db_creates_the_directory_sqlite_will_not(tmp_path):
    """A deploy-time failure, not a development one.

    DATABASE_URL on the deployed instance points at a path under /tmp that
    nothing has created yet. SQLite does not create missing directories, so
    without this the first query is "unable to open database file".
    """
    nested = tmp_path / "does" / "not" / "exist" / "depwatch.db"
    init_db(f"sqlite:///{nested}")

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Package)) == 0
    assert nested.exists()


def test_init_db_leaves_an_in_memory_url_alone(tmp_path):
    """No file, no directory to create, and no crash trying."""
    init_db("sqlite://")
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Package)) == 0


# The queries the API actually runs, with the shapes it runs them in.
HOT_QUERIES = {
    "analyse: packages by name": (
        "SELECT * FROM packages WHERE ecosystem = 'npm' AND name IN ('lodash', 'express')"
    ),
    "analyse: advisories by package": (
        "SELECT * FROM advisories WHERE ecosystem = 'npm'"
        " AND package_name IN ('lodash', 'express')"
    ),
    "packages: the ecosystem, ordered": (
        "SELECT * FROM packages WHERE ecosystem = 'npm' ORDER BY name"
    ),
    "packages: advisories for the ecosystem": (
        "SELECT * FROM advisories WHERE ecosystem = 'npm'"
    ),
}


def test_every_hot_query_uses_an_index(tmp_path):
    """No query the API runs may fall back to a table scan.

    There are no hand-written indexes in the schema, and that is the finding
    rather than an omission: the unique constraints on (ecosystem, name) and
    (ecosystem, package_name, osv_id) index exactly the columns these queries
    filter and order by, so adding more would cost writes and buy nothing.

    This test exists so that stays true. Add a query that filters on something
    unindexed - a licence, a date, a severity - and it fails here rather than
    at whatever point the table gets big enough to notice.
    """
    url = f"sqlite:///{tmp_path / 'plans.db'}"
    init_db(url)

    with session_scope(url) as session:
        for label, query in HOT_QUERIES.items():
            plan = " ".join(
                row[3] for row in session.execute(text("EXPLAIN QUERY PLAN " + query))
            )
            assert "USING INDEX" in plan, f"{label} is not using an index: {plan}"
            assert "SCAN" not in plan, f"{label} scans the table: {plan}"


def test_the_latest_run_is_read_without_sorting(tmp_path):
    """/healthz reads the newest run on every health check.

    It scans rather than using a named index, which is correct: `id` is an
    INTEGER PRIMARY KEY, so the table is already in that order and the engine
    walks it backwards and stops at the first row. The thing that would hurt is
    a sort, so that is what this asserts the absence of.
    """
    url = f"sqlite:///{tmp_path / 'runs.db'}"
    init_db(url)

    with session_scope(url) as session:
        plan = " ".join(
            row[3]
            for row in session.execute(
                text("EXPLAIN QUERY PLAN SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 1")
            )
        )
    assert "TEMP B-TREE" not in plan.upper(), f"the latest run is being sorted: {plan}"
