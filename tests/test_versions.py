"""Version comparison and advisory applicability.

The rule that matters: when we cannot tell, the advisory still counts.
Failing towards "safe" on a security signal is the wrong way to be wrong.
"""

import pytest

from depwatch.versions import advisory_applies, is_at_least, parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.2.3", (1, 2, 3, True)),
        ("v1.2.3", (1, 2, 3, True)),
        ("4.19", (4, 19, 0, True)),
        ("4.0.0-rc1", (4, 0, 0, False)),
        ("1.2.3+build.5", (1, 2, 3, True)),
        ("  2.0.0  ", (2, 0, 0, True)),
    ],
)
def test_parses_common_shapes(text, expected):
    assert parse(text) == expected


@pytest.mark.parametrize("text", [None, "", "latest", "next", "^1.2.3", "1.x", 4])
def test_unparseable_returns_none(text):
    assert parse(text) is None


def test_prerelease_sorts_below_its_release():
    assert is_at_least("4.0.0", "4.0.0-rc1") is True
    assert is_at_least("4.0.0-rc1", "4.0.0") is False


def test_ordering_across_components():
    assert is_at_least("5.3.0", "4.19.2") is True
    assert is_at_least("4.18.0", "4.19.2") is False
    assert is_at_least("4.19.2", "4.19.2") is True


def test_comparison_is_none_when_either_side_is_unparseable():
    assert is_at_least("latest", "1.0.0") is None
    assert is_at_least("1.0.0", "latest") is None


def test_advisory_with_no_fix_always_applies():
    assert advisory_applies(None, "99.0.0") is True
    assert advisory_applies("", "99.0.0") is True


def test_advisory_does_not_apply_once_the_fix_is_in():
    """The express case: every advisory fixed by 4.20.0, shipping 5.3.0."""
    assert advisory_applies("4.19.2", "5.3.0") is False
    assert advisory_applies("4.20.0", "5.3.0") is False


def test_advisory_still_applies_below_the_fix():
    assert advisory_applies("4.19.2", "4.18.0") is True


def test_unparseable_versions_keep_the_advisory():
    assert advisory_applies("4.19.2", None) is True
    assert advisory_applies("4.19.2", "latest") is True
    assert advisory_applies("not-a-version", "5.3.0") is True


# --- PEP 440, met when PyPI was added as a second ecosystem -----------------

@pytest.mark.parametrize(
    "version",
    ["0.66b2", "2.0.0rc1", "1.10a1", "2.34.0.dev1", "0.4.0beta1", "0.6b1", "1.0c1"],
)
def test_pep440_prereleases_parse_and_are_marked_as_prereleases(version):
    """PEP 440 attaches the marker with no separator where semver requires a
    hyphen. Measured at 1.7% of the latest releases of the top 120 PyPI
    packages before this was handled."""
    parsed = parse(version)
    assert parsed is not None, version
    assert parsed[3] is False, f"{version} should be a prerelease"


def test_semver_prereleases_still_parse():
    """The npm spelling must not regress while making room for the PyPI one."""
    assert parse("5.4.0-beta.1") == (5, 4, 0, False)
    assert parse("4.0.0-rc.1") == (4, 0, 0, False)


def test_releases_are_still_releases():
    assert parse("4.17.11") == (4, 17, 11, True)
    assert parse("1.2.3+build.5") == (1, 2, 3, True)
    assert parse("v2.3.4") == (2, 3, 4, True)


def test_a_prerelease_sorts_below_its_release():
    assert parse("2.0.0rc1") < parse("2.0.0")
    assert is_at_least("2.0.0rc1", "2.0.0") is False
    assert is_at_least("2.0.0", "2.0.0rc1") is True


def test_calendar_versions_remain_unsupported():
    """Four components would need variable-length release tuples, which changes
    the shape every caller compares. It fails in the safe direction instead."""
    assert parse("2026.9.21.13") is None
    assert advisory_applies(fixed_version="2026.9.22", current_version="2026.9.21.13") is True


def test_post_releases_remain_unsupported_on_purpose():
    """A post-release sorts *after* its release, so treating it as equal is the
    one direction that would exonerate a vulnerable version."""
    assert parse("1.0.post1") is None
    assert advisory_applies(fixed_version="1.0", current_version="1.0.post1") is True
