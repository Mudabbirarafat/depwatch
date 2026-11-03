"""PyPI source.

The second ecosystem, and the reason for adding it: a pluggable-source design
is a claim until something else is plugged into it. Everything PyPI does
differently from npm is a place where the abstraction leaked, and those are
recorded here rather than smoothed over.

Where it differs from npm, confirmed against the live API:

* **Maintainers live under a separate `ownership` key**, not in `info`. npm
  puts a `maintainers` array on the document. Present for about 92% of the top
  packages; `None` when absent, which the scorer already tolerates.
* **There is no deprecation flag.** npm has one. The closest signals are a
  yanked latest release and the `Development Status :: 7 - Inactive`
  classifier, and neither means quite the same thing - a yank is per release,
  not per project. Both are treated as deprecated and the difference is stated
  in the README rather than hidden.
* **`info.license` is sometimes the entire licence text**, not an identifier.
  The column is 128 characters, so a long value is dropped in favour of
  nothing rather than truncated into something that looks like an identifier
  but is not. `license_expression` is preferred when present, being the SPDX
  field.
* **`releases` contains versions with no files.** Those are releases whose
  files were deleted; they cannot be installed, so including them would let a
  range resolve to a version that does not exist.
* **Versions are PEP 440, not semver** - see `versions.py` for what that costs.
* **Names are case-insensitive and normalised** (PEP 503), and the API answers
  with the canonical display name rather than what you asked for. See
  `normalise` below and the note in `sources/__init__.py`.
"""

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, field_validator

ECOSYSTEM = "pypi"

# PEP 503: runs of -, _ and . collapse to a single -, then lowercase.
_SEPARATORS = re.compile(r"[-_.]+")


def normalise(name: str) -> str:
    """The PEP 503 form, which is what PyPI treats as the project's identity.

    `Django`, `django` and `DJANGO` are one project; `zope.interface` and
    `zope-interface` are one project. Storing and querying anything other than
    this form means a lookup can miss a package that was ingested.
    """
    return _SEPARATORS.sub("-", name.strip()).lower()

# Anything longer is prose, not an identifier. "GNU General Public License v3
# or later (GPLv3+)" is 48 characters, so this is generous.
MAX_LICENSE_LENGTH = 128

INACTIVE_CLASSIFIER = "Development Status :: 7 - Inactive"

# In preference order. PyPI projects use these keys inconsistently.
REPOSITORY_KEYS = ("Source", "Source Code", "Repository", "Homepage", "Home")


class PypiPackage(BaseModel):
    """Validated projection of one PyPI document. Mirrors NpmPackage."""

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


def _license(info: dict) -> str | None:
    """SPDX expression if there is one, else the legacy field if it is short.

    `info.license` carries the full licence text for a surprising number of
    projects. Truncating that to 128 characters would store something that
    reads like an identifier and is not, so a long value is discarded and the
    package is treated as having no declared licence - which is what the
    scorer would conclude from the text anyway.
    """
    expression = info.get("license_expression")
    if isinstance(expression, str) and expression.strip():
        return expression.strip()

    legacy = info.get("license")
    if isinstance(legacy, str) and legacy.strip():
        value = legacy.strip()
        return value if len(value) <= MAX_LICENSE_LENGTH else None
    return None


def _repository_url(info: dict) -> str | None:
    urls = info.get("project_urls")
    if isinstance(urls, dict):
        for key in REPOSITORY_KEYS:
            value = urls.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    home = info.get("home_page")
    return home.strip() if isinstance(home, str) and home.strip() else None


def _maintainer_count(document: dict) -> int | None:
    """From the top-level `ownership` block, which npm has no equivalent of.

    An empty `roles` list is reported as unknown rather than zero. A published
    package always has someone able to publish it, so zero never means "nobody
    maintains this" - it means PyPI did not say. The common cause is an
    organisation-owned project: Django's ownership is
    `{"organization": "django", "roles": []}`, and counting that as zero made
    the scorer call one of the best-maintained packages in Python
    single-maintainer.
    """
    ownership = document.get("ownership") or {}
    roles = ownership.get("roles")
    if not isinstance(roles, list) or not roles:
        return None
    return len(roles)


def _is_deprecated(info: dict) -> bool:
    if info.get("yanked"):
        return True
    classifiers = info.get("classifiers")
    return isinstance(classifiers, list) and INACTIVE_CLASSIFIER in classifiers


def _installable_versions(releases: Any) -> list[str]:
    """Versions that still have files. The rest cannot be installed."""
    if not isinstance(releases, dict):
        return []
    return sorted(version for version, files in releases.items() if files)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        # PyPI sends a trailing 'Z'. fromisoformat has read that since 3.11,
        # which this project already requires.
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _last_published(releases: Any, latest: str | None) -> datetime | None:
    """The newest upload time among the latest release's files."""
    if not isinstance(releases, dict) or not latest:
        return None
    times = [
        _parse_time(f.get("upload_time_iso_8601") or f.get("upload_time"))
        for f in releases.get(latest) or []
        if isinstance(f, dict)
    ]
    found = [t for t in times if t is not None]
    return max(found) if found else None


def parse(document: dict) -> PypiPackage:
    """Project a PyPI document onto PypiPackage.

    Raises pydantic.ValidationError when the document cannot be used, which the
    ingest loop turns into a quarantined record rather than a failed run.
    """
    info = document.get("info") or {}
    releases = document.get("releases")

    # The document answers with the canonical display name, so normalise it:
    # the stored name is a key, and keys have to be comparable.
    raw_name = info.get("name") or ""

    return PypiPackage(
        name=normalise(raw_name) if raw_name else "",
        latest_version=info.get("version"),
        description=info.get("summary"),
        license=_license(info),
        repository_url=_repository_url(info),
        maintainers=_maintainer_count(document),
        deprecated=_is_deprecated(info),
        known_versions=_installable_versions(releases),
        last_published_at=_last_published(releases, info.get("version")),
    )


def package_url(registry: str, name: str) -> str:
    return f"{registry.rstrip('/')}/pypi/{name}/json"
