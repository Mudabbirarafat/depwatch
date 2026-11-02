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
