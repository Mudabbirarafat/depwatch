"""Engine, session and the upsert.

The upsert is the piece worth reading. Ingestion must be safe to run twice -
reruns happen after a crash, a backfill, or a retried CI job - so writes are
keyed on (ecosystem, name) and update in place rather than inserting.

`first_seen_at` is deliberately excluded from the update: it records when we
first learned about a package and must survive every later refresh.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from depwatch.config import settings
from depwatch.models import Base, Package

_engine = None
_Session = None


def engine(url: str | None = None):
    global _engine, _Session
    if _engine is None or url is not None:
        _engine = create_engine(url or settings.database_url, future=True)
        _Session = sessionmaker(bind=_engine, future=True)
    return _engine


def init_db(url: str | None = None) -> None:
    Base.metadata.create_all(engine(url))


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    engine(url)
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def upsert_packages(session: Session, rows: Iterable[dict]) -> int:
    """Insert or refresh packages. Returns the number of rows written.

    Both SQLite (3.24+) and Postgres support ON CONFLICT DO UPDATE, so the only
    difference is which insert construct builds it.
    """
    rows = [dict(r) for r in rows]
    if not rows:
        return 0

    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(Package).values(rows)

    updatable = {
        c.name: getattr(stmt.excluded, c.name)
        for c in Package.__table__.columns
        # never overwrite the surrogate key or the first-seen timestamp
        if c.name not in {"id", "ecosystem", "name", "first_seen_at"}
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=[Package.ecosystem, Package.name], set_=updatable
    )
    session.execute(stmt)
    return len(rows)
