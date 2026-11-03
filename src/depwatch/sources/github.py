"""GitHub as an enrichment source.

Not an ecosystem - nothing is ingested *from* GitHub. This enriches packages
already in the warehouse with the signals the registries do not carry, and
which the README has been calling the strongest maintenance indicators while
not having them: when the repository was last pushed to, how many people
contribute to it, and whether it has been archived.

**Archived is the signal worth the whole module.** npm has a deprecation flag
that most abandoned packages never set, and PyPI has none at all - but a
maintainer who has given up very often archives the repository, and that is a
deliberate, unambiguous statement. It is the only *declared* end-of-life signal
available for PyPI packages.

Two awkward parts, both real:

* **Registry repository URLs are not URLs**, quite. Observed in live data:
  `git+https://github.com/chalk/chalk.git`, `git://github.com/x/y.git`,
  `git+ssh://git@github.com/stevemao/left-pad.git`,
  `http://github.com/fsspec/s3fs/`, and npm's `github:owner/repo` shorthand.
  `owner_repo` absorbs those.
* **Counting contributors costs one request, not a pagination loop.** Asking
  for one contributor per page makes GitHub report the last page number in the
  `Link` header, and the last page number of a one-per-page list *is* the
  count.
"""

import os
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

API = "https://api.github.com"

# Anything else - GitLab, Bitbucket, a personal host - is left unenriched
# rather than guessed at. Measured on 408 real packages: 335 resolve to GitHub,
# 27 to something else, and 45 carry no repository URL at all.
HOST = "github.com"

_SCHEME = re.compile(r"^(?:git\+)?(?:https?|git|ssh)://", re.IGNORECASE)
_SCP = re.compile(r"^(?:git\+)?(?:ssh://)?git@", re.IGNORECASE)
_SHORTHAND = re.compile(r"^github:(?P<owner>[^/]+)/(?P<repo>[^/]+)$", re.IGNORECASE)
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

# The page number GitHub reports as the last one, in a Link header.
_LAST_PAGE = re.compile(r'[?&]page=(\d+)[^>]*>;\s*rel="last"')


class Repository(BaseModel):
    """The projection of a repository that the risk model can use."""

    full_name: str
    archived: bool = False
    pushed_at: datetime | None = None
    stars: int | None = None
    open_issues: int | None = None

    model_config = {"extra": "ignore"}

    @field_validator("full_name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        # Same rule as the registry sources: an unusable document is rejected
        # here so the caller can record it rather than storing a blank.
        if not value or not value.strip():
            raise ValueError("repository full_name is empty")
        return value.strip()


def owner_repo(url: str | None) -> tuple[str, str] | None:
    """(owner, repo) from whatever a registry called a repository URL.

    Returns None for anything not on GitHub, which the caller treats as "not
    enrichable" rather than as an error - most packages that fail here are
    hosted somewhere else, which is a fact about them and not a fault.
    """
    if not isinstance(url, str) or not url.strip():
        return None

    text = url.strip()

    shorthand = _SHORTHAND.match(text)
    if shorthand:
        return _clean(shorthand["owner"], shorthand["repo"])

    # git@github.com:owner/repo.git - an SCP-like path, not a URL
    if _SCP.match(text):
        text = _SCP.sub("", text).replace(":", "/", 1)
    else:
        text = _SCHEME.sub("", text)
        if text.lower().startswith("git+"):
            text = text[4:]

    text = text.split("#", 1)[0].split("?", 1)[0]
    host, _, path = text.partition("/")
    if host.lower().removeprefix("www.") != HOST:
        return None

    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return _clean(parts[0], parts[1])


def _clean(owner: str, repo: str) -> tuple[str, str] | None:
    owner = owner.strip()
    repo = repo.strip().removesuffix(".git")
    if not _SEGMENT.match(owner) or not _SEGMENT.match(repo):
        return None
    return owner, repo


def repo_url(owner: str, repo: str) -> str:
    return f"{API}/repos/{owner}/{repo}"


def contributors_url(owner: str, repo: str) -> str:
    # One per page, so the last page number is the contributor count.
    # anon=1 includes commits not linked to a GitHub account.
    return f"{API}/repos/{owner}/{repo}/contributors?per_page=1&anon=1"


def headers() -> dict[str, str]:
    """Auth when a token is configured.

    Unauthenticated GitHub allows 60 requests an hour, which enriches thirty
    packages and then stops. With a token it is 5,000. The caller checks
    `authenticated()` and says so rather than discovering it mid-run.
    """
    sent = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        sent["Authorization"] = f"Bearer {token}"
    return sent


def authenticated() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN", "").strip())


def parse_repo(document: dict) -> Repository:
    """Project a /repos response. Raises ValidationError if it is unusable."""
    return Repository(
        full_name=document.get("full_name") or "",
        archived=bool(document.get("archived")),
        pushed_at=_parse_time(document.get("pushed_at")),
        stars=document.get("stargazers_count"),
        open_issues=document.get("open_issues_count"),
    )


def contributor_count(payload: Any, link_header: str | None) -> int | None:
    """The contributor count, from the last page number of a 1-per-page list.

    A repository with one contributor returns a single-element list and no
    `Link` header at all, so the fallback is the length of what came back.
    """
    if link_header:
        match = _LAST_PAGE.search(link_header)
        if match:
            return int(match.group(1))
    if isinstance(payload, list):
        return len(payload)
    return None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
