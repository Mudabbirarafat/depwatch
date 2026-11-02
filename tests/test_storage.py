"""Storage bootstrap.

The upsert behaviour is covered in test_ingest.py; what is tested here is
getting a usable database in the first place, which is where the deployed
instance differs from a development machine.
"""

from sqlalchemy import func, select

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
