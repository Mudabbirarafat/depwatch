"""GitHub enrichment: URL shapes, the contributor trick, and the payload.

The URL cases are not invented. Every one of them was observed in the
`repository_url` column after ingesting 408 real npm and PyPI packages.
"""

import pytest
from pydantic import ValidationError

from depwatch.sources import github


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Observed in live registry data, all six of these
        ("https://github.com/certifi/python-certifi", ("certifi", "python-certifi")),
        ("https://github.com/stub42/pytz.git", ("stub42", "pytz")),
        ("http://github.com/fsspec/s3fs/", ("fsspec", "s3fs")),
        ("git+https://github.com/chalk/chalk.git", ("chalk", "chalk")),
        ("git+ssh://git@github.com/stevemao/left-pad.git", ("stevemao", "left-pad")),
        ("git://github.com/minimistjs/minimist.git", ("minimistjs", "minimist")),
        # Other shapes the registries allow
        ("git@github.com:owner/repo.git", ("owner", "repo")),
        ("github:owner/repo", ("owner", "repo")),
        ("https://www.github.com/a/b", ("a", "b")),
        ("https://github.com/a/b#readme", ("a", "b")),
        ("https://github.com/a/b?tab=readme", ("a", "b")),
        ("https://github.com/a/b/tree/main/packages/c", ("a", "b")),
        ("  https://github.com/a/b  ", ("a", "b")),
    ],
)
def test_owner_and_repo_come_out_of_whatever_the_registry_called_a_url(url, expected):
    assert github.owner_repo(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/a/b",
        "https://bitbucket.org/a/b",
        "https://example.com/a/b",
        "https://github.com/onlyowner",
        "https://github.com/",
        "not a url",
        "",
        "   ",
        None,
        123,
    ],
)
def test_anything_not_a_github_repository_is_left_alone(url):
    """Not an error. Most of these are packages hosted somewhere else, which
    is a fact about them rather than a fault."""
    assert github.owner_repo(url) is None


def test_a_segment_with_impossible_characters_is_rejected():
    assert github.owner_repo("https://github.com/a b/c") is None


class TestContributorCount:
    """The count comes from the last page of a one-per-page list.

    Asking for every contributor would mean paginating through hundreds of
    names to learn one number; asking for one makes GitHub report the page
    count, and for a one-per-page list that *is* the count.
    """

    def test_the_last_page_number_is_the_count(self):
        link = (
            '<https://api.github.com/repositories/1/contributors?per_page=1&page=2>; rel="next", '
            '<https://api.github.com/repositories/1/contributors?per_page=1&page=795>; rel="last"'
        )
        assert github.contributor_count([{"login": "a"}], link) == 795

    def test_a_single_contributor_returns_no_link_header(self):
        """GitHub omits Link entirely when there is only one page."""
        assert github.contributor_count([{"login": "solo"}], None) == 1

    def test_an_empty_repository_has_no_contributors(self):
        assert github.contributor_count([], None) == 0

    def test_an_unexpected_payload_is_unknown_rather_than_zero(self):
        assert github.contributor_count({"message": "nope"}, None) is None

    def test_a_link_header_without_a_last_page_falls_back_to_the_body(self):
        assert github.contributor_count([{"login": "a"}], '<...>; rel="next"') == 1


def test_the_repository_projection():
    repository = github.parse_repo(
        {
            "full_name": "psf/requests",
            "archived": False,
            "pushed_at": "2026-11-02T10:00:00Z",
            "stargazers_count": 54594,
            "open_issues_count": 7,
            "ignored_field": "...",
        }
    )

    assert repository.full_name == "psf/requests"
    assert repository.archived is False
    assert repository.pushed_at.year == 2026
    assert repository.stars == 54594
    assert repository.open_issues == 7


def test_an_archived_repository_is_recorded_as_such():
    """The signal worth the whole module: PyPI has no deprecation flag, and
    archiving is a deliberate statement that a project is over."""
    assert github.parse_repo({"full_name": "a/b", "archived": True}).archived is True


def test_a_repository_with_no_name_is_rejected():
    with pytest.raises(ValidationError):
        github.parse_repo({"archived": False})


def test_an_unparseable_push_time_is_dropped_not_fatal():
    assert github.parse_repo({"full_name": "a/b", "pushed_at": "whenever"}).pushed_at is None


def test_missing_counts_are_unknown():
    repository = github.parse_repo({"full_name": "a/b"})
    assert repository.stars is None
    assert repository.open_issues is None


def test_the_urls():
    assert github.repo_url("a", "b") == "https://api.github.com/repos/a/b"
    assert "per_page=1" in github.contributors_url("a", "b")
    assert "anon=1" in github.contributors_url("a", "b")


def test_the_token_is_sent_when_configured(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "a-test-token")
    assert github.headers()["Authorization"] == "Bearer a-test-token"
    assert github.authenticated() is True


def test_no_token_means_no_authorization_header(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert "Authorization" not in github.headers()
    assert github.authenticated() is False


def test_a_blank_token_counts_as_no_token(monkeypatch):
    """An empty environment variable is how a half-configured deploy looks."""
    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    assert github.authenticated() is False
    assert "Authorization" not in github.headers()
