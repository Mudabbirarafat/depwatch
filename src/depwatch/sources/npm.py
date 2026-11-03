"""npm registry source.

registry.npmjs.org returns one large document per package containing every
version ever published. We keep a small, stable projection of it; the full
payload goes to the raw zone so a later schema change can be backfilled from
disk instead of re-crawling the registry.

Registry payloads are inconsistent in practice: `repository` is sometimes a
string and sometimes an object, `license` is sometimes a dict, `maintainers`
is sometimes absent. The model below absorbs that rather than crashing a run.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, field_validator

ECOSYSTEM = "npm"


def normalise(name: str) -> str:
    """Identity, deliberately.

    npm answers with the name you asked for, and new package names must already
    be lowercase, so there is nothing to reconcile. This exists so that every
    source answers the same question - PyPI's answer is the interesting one.
    """
    return name.strip()


class NpmPackage(BaseModel):
    """Validated projection of one registry document."""

    name: str
    latest_version: str | None = None
    description: str | None = None
    license: str | None = None
    repository_url: str | None = None
    maintainers: int | None = None
    deprecated: bool = False
    last_published_at: datetime | None = None
    known_versions: list[str] = []

    model_config = {"extra": "ignore"}

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("package name is empty")
        return value.strip()

    def to_row(self, fetched_at: datetime | None = None) -> dict:
        now = fetched_at or datetime.now(UTC)
        return {
            "ecosystem": ECOSYSTEM,
            "name": self.name,
            "latest_version": self.latest_version,
            "description": self.description,
            "license": self.license,
            "repository_url": self.repository_url,
            "maintainers": self.maintainers,
            "deprecated": self.deprecated,
            "last_published_at": self.last_published_at,
            "known_versions": self.known_versions,
            "first_seen_at": now,
            "fetched_at": now,
        }


def _repository_url(repository: Any) -> str | None:
    if isinstance(repository, str):
        return repository or None
    if isinstance(repository, dict):
        return repository.get("url") or None
    return None


def _license(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):  # the old {"type": "MIT"} shape
        return value.get("type") or None
    if isinstance(value, list) and value:
        return _license(value[0])
    return None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse(document: dict) -> NpmPackage:
    """Project a registry document onto NpmPackage.

    Raises pydantic.ValidationError when the document cannot be used, which the
    ingest loop turns into a quarantined record rather than a failed run.
    """
    latest = (document.get("dist-tags") or {}).get("latest")
    versions = document.get("versions") or {}
    latest_doc = versions.get(latest) or {}

    times = document.get("time") or {}
    published = _parse_time(times.get(latest)) or _parse_time(times.get("modified"))

    maintainers = document.get("maintainers")
    maintainer_count = len(maintainers) if isinstance(maintainers, list) else None

    return NpmPackage(
        name=document.get("name") or "",
        latest_version=latest,
        description=document.get("description") or latest_doc.get("description"),
        license=_license(document.get("license") or latest_doc.get("license")),
        repository_url=_repository_url(
            document.get("repository") or latest_doc.get("repository")
        ),
        maintainers=maintainer_count,
        deprecated=bool(latest_doc.get("deprecated")),
        known_versions=sorted(versions.keys()) if isinstance(versions, dict) else [],
        last_published_at=published,
    )


def package_url(registry: str, name: str) -> str:
    # scoped packages (@scope/name) must have the slash encoded
    return f"{registry.rstrip('/')}/{name.replace('/', '%2f')}"
