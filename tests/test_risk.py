"""Risk scoring rules.

Scoring is a pure function, so every rule is asserted directly rather than
inferred from an end-to-end result. A score nobody can explain is a score
nobody will trust, and these tests are the explanation.
"""

from datetime import UTC, datetime, timedelta

from depwatch.risk import band_for, score_package

NOW = datetime(2026, 11, 2, tzinfo=UTC)


def package(**overrides) -> dict:
    base = {
        "name": "thing",
        "license": "MIT",
        "maintainers": 4,
        "deprecated": False,
        "last_published_at": NOW - timedelta(days=30),
    }
    return {**base, **overrides}


def advisory(severity="HIGH", fixed="1.2.3", osv_id="GHSA-xxxx"):
    return {"osv_id": osv_id, "severity": severity, "fixed_version": fixed}


def test_a_healthy_package_scores_zero():
    result = score_package(package(), [], now=NOW)
    assert result.score == 0
    assert result.band == "low"
    assert result.reasons == []


def test_a_critical_advisory_dominates_the_score():
    result = score_package(package(), [advisory("CRITICAL")], now=NOW)
    assert result.score == 50
    assert result.band == "medium"
    assert "critical severity advisory" in result.reasons[0]


def test_extra_advisories_add_but_are_capped():
    many = [advisory("HIGH", osv_id=f"GHSA-{i}") for i in range(10)]
    result = score_package(package(), many, now=NOW)
    # 35 for the worst + 20 cap for the rest, not 35 * 10
    assert result.score == 55


def test_an_advisory_with_no_fix_costs_extra():
    with_fix = score_package(package(), [advisory("HIGH", fixed="2.0.0")], now=NOW).score
    without = score_package(package(), [advisory("HIGH", fixed=None)], now=NOW).score
    assert without > with_fix


def test_deprecated_is_heavily_penalised():
    result = score_package(package(deprecated=True), [], now=NOW)
    assert result.score == 40
    assert "deprecated" in " ".join(result.reasons)


def test_abandoned_beats_merely_stale():
    stale = score_package(
        package(last_published_at=NOW - timedelta(days=400)), [], now=NOW
    )
    abandoned = score_package(
        package(last_published_at=NOW - timedelta(days=900)), [], now=NOW
    )
    assert abandoned.score > stale.score
    assert "no release in over a year" in stale.reasons
    assert "no release in 2 years" in abandoned.reasons


def test_recent_release_is_not_penalised():
    assert score_package(package(last_published_at=NOW - timedelta(days=10)), [], now=NOW).score == 0


def test_single_maintainer_is_a_risk():
    assert score_package(package(maintainers=1), [], now=NOW).score == 15
    assert score_package(package(maintainers=2), [], now=NOW).score == 0


def test_missing_licence_is_a_risk():
    assert score_package(package(license=None), [], now=NOW).score == 10


def test_unknown_maintainer_count_is_not_punished():
    """Absent data is not evidence of a problem."""
    assert score_package(package(maintainers=None), [], now=NOW).score == 0


def test_risks_accumulate_into_the_high_band():
    result = score_package(
        package(
            deprecated=True,
            maintainers=1,
            license=None,
            last_published_at=NOW - timedelta(days=1000),
        ),
        [advisory("CRITICAL", fixed=None)],
        now=NOW,
    )
    assert result.band == "high"
    assert len(result.reasons) >= 5


def test_score_is_clamped_to_100():
    many = [advisory("CRITICAL", fixed=None, osv_id=f"G-{i}") for i in range(20)]
    result = score_package(
        package(deprecated=True, maintainers=1, license=None,
                last_published_at=NOW - timedelta(days=3000)),
        many,
        now=NOW,
    )
    assert result.score == 100


def test_ungraded_advisories_do_not_crash_scoring():
    """OSV only grades GitHub-reviewed advisories; the rest have no severity."""
    result = score_package(package(), [advisory(severity=None)], now=NOW)
    assert result.score >= 0


def test_band_boundaries():
    assert band_for(0) == "low"
    assert band_for(29) == "low"
    assert band_for(30) == "medium"
    assert band_for(59) == "medium"
    assert band_for(60) == "high"
    assert band_for(100) == "high"


def test_advisories_already_fixed_in_the_current_version_are_ignored():
    """The express case. Five advisories, all fixed by 4.20.0, shipping 5.3.0:
    a well-maintained package must not be penalised forever."""
    advisories = [
        advisory("MODERATE", fixed="4.19.2", osv_id="GHSA-a"),
        advisory("LOW", fixed="4.20.0", osv_id="GHSA-b"),
    ]
    result = score_package(package(latest_version="5.3.0"), advisories, now=NOW)
    assert result.score == 0
    assert result.band == "low"
    assert "all fixed in the current version" in " ".join(result.reasons)


def test_advisories_not_yet_fixed_in_the_current_version_still_count():
    advisories = [advisory("CRITICAL", fixed="5.0.0", osv_id="GHSA-c")]
    result = score_package(package(latest_version="4.18.0"), advisories, now=NOW)
    assert result.score >= 50


def test_an_unfixed_advisory_counts_regardless_of_version():
    result = score_package(
        package(latest_version="99.0.0"), [advisory("HIGH", fixed=None)], now=NOW
    )
    assert result.score > 0


def test_unknown_current_version_keeps_advisories():
    """If we cannot tell which version is current, do not exonerate it."""
    result = score_package(
        package(latest_version=None), [advisory("CRITICAL", fixed="1.0.0")], now=NOW
    )
    assert result.score >= 50


def test_an_advisory_bounded_from_above_does_not_count_as_unfixed():
    """The Django case: a 2012 advisory affecting up to 1.3.1 named no fix, so
    it read as "never fixed" and penalised version 6 forever."""
    result = score_package(
        {"name": "django", "latest_version": "6.1.2", "maintainers": 12, "license": "BSD-3-Clause"},
        [{"osv_id": "GHSA-old", "severity": "HIGH", "fixed_version": None, "last_affected": "1.3.1"}],
    )

    assert result.score == 0, result.reasons
    assert not any("no published fix" in r for r in result.reasons)


def test_an_advisory_bounded_above_still_counts_below_that_bound():
    result = score_package(
        {"name": "django", "latest_version": "1.3.0", "maintainers": 12, "license": "BSD-3-Clause"},
        [{"osv_id": "GHSA-old", "severity": "HIGH", "fixed_version": None, "last_affected": "1.3.1"}],
    )

    assert result.score > 0
    assert any("high severity advisory" in r for r in result.reasons)


def test_an_advisory_with_neither_a_fix_nor_a_bound_still_counts():
    """Genuinely unfixed advisories must keep their penalty."""
    result = score_package(
        {"name": "x", "latest_version": "2.0.0", "maintainers": 5, "license": "MIT"},
        [{"osv_id": "GHSA-open", "severity": "CRITICAL", "fixed_version": None, "last_affected": None}],
    )

    assert any("no published fix" in r for r in result.reasons)


def test_unknown_maintainer_count_is_not_treated_as_single():
    """PyPI reports no maintainers for organisation-owned projects."""
    result = score_package(
        {"name": "django", "latest_version": "6.1.2", "maintainers": None, "license": "BSD-3-Clause"},
        [],
    )

    assert not any("single maintainer" in r for r in result.reasons)


# --- GitHub signals ---------------------------------------------------------

def test_an_archived_repository_is_treated_like_a_deprecation():
    """The only *declared* end-of-life signal a PyPI package can have: npm's
    deprecation flag is rarely set and PyPI has none at all."""
    result = score_package(
        {"name": "x", "latest_version": "1.0.0", "maintainers": 5, "license": "MIT",
         "repo_archived": True},
        [],
    )

    assert result.score >= 40
    assert "repository is archived" in result.reasons


def test_the_last_commit_is_preferred_over_the_last_release():
    """A package can go a year without a release while being actively worked
    on. pyyaml is exactly that, and counting the release date called it stale."""
    now = datetime(2026, 11, 3, tzinfo=UTC)
    package = {
        "name": "pyyaml", "latest_version": "6.0.3", "maintainers": 3, "license": "MIT",
        "last_published_at": now - timedelta(days=500),   # no release in over a year
        "repo_pushed_at": now - timedelta(days=20),       # but worked on last month
    }

    result = score_package(package, [], now=now)

    assert result.score == 0, result.reasons
    assert not any("release" in r for r in result.reasons)


def test_the_release_date_is_used_when_there_is_no_commit_date():
    """Unenriched packages must keep scoring exactly as they did."""
    now = datetime(2026, 11, 3, tzinfo=UTC)
    result = score_package(
        {"name": "x", "latest_version": "1.0.0", "maintainers": 5, "license": "MIT",
         "last_published_at": now - timedelta(days=500)},
        [],
        now=now,
    )

    assert any("no release in over a year" in r for r in result.reasons)


def test_a_dead_repository_is_counted_once_not_twice():
    """Both dates being old is corroboration, not two separate problems."""
    now = datetime(2026, 11, 3, tzinfo=UTC)
    result = score_package(
        {"name": "x", "latest_version": "1.0.0", "maintainers": 5, "license": "MIT",
         "last_published_at": now - timedelta(days=1100),
         "repo_pushed_at": now - timedelta(days=1100)},
        [],
        now=now,
    )

    staleness = [r for r in result.reasons if "no commit" in r or "no release" in r]
    assert len(staleness) == 1
    assert "no commit in 3 years" in staleness[0]


def test_one_contributor_and_one_maintainer_are_different_measurements():
    """Who may publish is not who writes the code. certifi has one maintainer
    and 43 contributors; both being one is worse than either."""
    now = datetime(2026, 11, 3, tzinfo=UTC)
    base = {"name": "x", "latest_version": "1.0.0", "license": "MIT",
            "last_published_at": now - timedelta(days=5)}

    many_authors = score_package({**base, "maintainers": 1, "repo_contributors": 43}, [], now=now)
    assert "single maintainer" in many_authors.reasons
    assert "one contributor" not in many_authors.reasons

    both = score_package({**base, "maintainers": 1, "repo_contributors": 1}, [], now=now)
    assert "one contributor" in both.reasons
    assert both.score > many_authors.score


def test_absent_github_signals_change_nothing():
    """82% of packages enrich; the rest must score exactly as before."""
    now = datetime(2026, 11, 3, tzinfo=UTC)
    package = {"name": "x", "latest_version": "1.0.0", "maintainers": 4, "license": "MIT",
               "last_published_at": now - timedelta(days=5)}

    without = score_package(package, [], now=now)
    with_nulls = score_package(
        {**package, "repo_archived": None, "repo_pushed_at": None, "repo_contributors": None},
        [], now=now,
    )

    assert without.score == with_nulls.score == 0
    assert without.reasons == with_nulls.reasons
