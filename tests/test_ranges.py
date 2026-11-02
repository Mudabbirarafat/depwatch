"""Resolving npm version ranges.

The rule that matters: an unsupported range is reported as unsupported, never
guessed at. A confident wrong answer about whether you are vulnerable is worse
than admitting the range was not understood.
"""

import pytest

from depwatch.ranges import bounds_for, resolve, satisfies

PUBLISHED = [
    "4.17.1", "4.18.0", "4.19.2", "4.20.0",
    "5.0.0", "5.3.0", "5.4.0-beta.1",
    "0.2.1", "0.2.9", "0.3.0", "0.0.3", "0.0.4",
]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("^4.17.1", "4.20.0"),   # caret stays inside the major
        ("~4.18.0", "4.18.0"),   # tilde stays inside the minor
        ("4.19.2", "4.19.2"),    # exact pin
        ("=4.19.2", "4.19.2"),
        (">=5.0.0", "5.3.0"),
        (">4.19.2", "5.3.0"),
        ("<4.19.0", "4.18.0"),
        ("<=4.18.0", "4.18.0"),
        ("4.x", "4.20.0"),
        ("5.x", "5.3.0"),
        ("*", "5.3.0"),
        ("", "5.3.0"),
        ("latest", "5.3.0"),
        (">= 4.20.0", "5.3.0"),  # whitespace after the operator
    ],
)
def test_resolves_common_ranges(spec, expected):
    assert resolve(spec, PUBLISHED) == expected


def test_caret_on_zero_minor_stays_inside_the_minor():
    """^0.2.1 may take 0.2.9 but not 0.3.0 - zero-major rules differ."""
    assert resolve("^0.2.1", PUBLISHED) == "0.2.9"


def test_caret_on_zero_zero_pins_exactly():
    assert resolve("^0.0.3", PUBLISHED) == "0.0.3"


def test_prereleases_are_not_resolved_to():
    """npm will not install 5.4.0-beta.1 for ^5.0.0 unless asked by name."""
    assert resolve("^5.0.0", PUBLISHED) == "5.3.0"


@pytest.mark.parametrize(
    "spec",
    ["^4 || ^5", "1.0.0 - 2.0.0", ">=4.0.0 <5.0.0", "workspace:*", "npm:other@^1", "file:../x"],
)
def test_unsupported_ranges_are_reported_not_guessed(spec):
    assert bounds_for(spec) is None
    assert resolve(spec, PUBLISHED) is None


def test_range_matching_nothing_published_resolves_to_none():
    assert resolve("^9.0.0", PUBLISHED) is None


def test_satisfies_boundaries():
    assert satisfies("4.17.1", "^4.17.1") is True
    assert satisfies("4.17.0", "^4.17.1") is False
    assert satisfies("5.0.0", "^4.17.1") is False
    assert satisfies("4.18.9", "~4.18.0") is True
    assert satisfies("4.19.0", "~4.18.0") is False


def test_satisfies_is_none_for_unsupported_input():
    assert satisfies("1.0.0", "^1 || ^2") is None
    assert satisfies("not-a-version", "^1.0.0") is None


def test_empty_version_list_resolves_to_none():
    assert resolve("^4.0.0", []) is None
    assert resolve("^4.0.0", None) is None
