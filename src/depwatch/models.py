"""Database schema.

Three tables, and the split matters:

* `packages` is the mart - one current row per package, upserted.
* `ingest_runs` is run metadata. Every run records what it saw and whether it
  finished. Without this a pipeline is a black box; with it the dashboard can
  show the last ten runs and a freshness check has something to read.
* `quarantine` holds records that failed validation, with the reason. A bad
  payload from a registry must not abort the run, and must not vanish either.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Package(Base):
    __tablename__ = "packages"
    __table_args__ = (UniqueConstraint("ecosystem", "name", name="uq_packages_eco_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ecosystem: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))

    latest_version: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(128))
    repository_url: Mapped[str | None] = mapped_column(Text)
    maintainers: Mapped[int | None] = mapped_column(Integer)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Needed to resolve a manifest range to the version actually installed.
    # A child table is the scalable shape; a JSON list is honest for now.
    known_versions: Mapped[list | None] = mapped_column(JSON)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requested: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    quarantined: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class Quarantine(Base):
    __tablename__ = "quarantine"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Advisory(Base):
    """A published security advisory affecting a package.

    Keyed on (ecosystem, package_name, osv_id) so re-querying OSV refreshes a
    known advisory rather than accumulating copies of it. Severity is taken
    from database_specific.severity, which is the word GitHub assigns
    (LOW / MODERATE / HIGH / CRITICAL); the CVSS vector is kept alongside for
    anyone who wants to recompute it.
    """

    __tablename__ = "advisories"
    __table_args__ = (
        UniqueConstraint("ecosystem", "package_name", "osv_id", name="uq_advisory"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ecosystem: Mapped[str] = mapped_column(String(32))
    package_name: Mapped[str] = mapped_column(String(255))
    osv_id: Mapped[str] = mapped_column(String(64))

    severity: Mapped[str | None] = mapped_column(String(16))
    cvss_vector: Mapped[str | None] = mapped_column(String(128))
    summary: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list | None] = mapped_column(JSON)
    fixed_version: Mapped[str | None] = mapped_column(String(64))
    # Set when the advisory bounds itself from above instead of naming a fix.
    # Without it such an advisory reads as "never fixed" - see sources/osv.py.
    last_affected: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
