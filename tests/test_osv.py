"""Parsing OSV advisory payloads.

Shapes below are taken from real /v1/query responses for lodash and react.
"""

from depwatch.sources import osv


def vuln(**overrides) -> dict:
    base = {
        "id": "GHSA-29mw-wpgm-hmr9",
        "summary": "Regular Expression Denial of Service (ReDoS) in lodash",
        "aliases": ["CVE-2020-28500"],
        "published": "2022-01-06T20:30:46Z",
        "database_specific": {"severity": "MODERATE"},
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:L"}],
        "affected": [
            {"ranges": [{"type": "SEMVER", "events": [{"introduced": "4.0.0"}, {"fixed": "4.17.21"}]}]}
        ],
    }
    return {**base, **overrides}


def test_parses_a_full_advisory():
    row = osv.parse_vuln(vuln(), package_name="lodash")
    assert row["osv_id"] == "GHSA-29mw-wpgm-hmr9"
    assert row["severity"] == "MODERATE"
    assert row["fixed_version"] == "4.17.21"
    assert row["aliases"] == ["CVE-2020-28500"]
    assert row["cvss_vector"].startswith("CVSS:3.1/")
    assert row["published_at"].year == 2022


def test_severity_is_absent_when_not_github_reviewed():
    row = osv.parse_vuln(vuln(database_specific={}), package_name="lodash")
    assert row["severity"] is None


def test_unrecognised_severity_word_is_dropped():
    row = osv.parse_vuln(
        vuln(database_specific={"severity": "SPICY"}), package_name="lodash"
    )
    assert row["severity"] is None


def test_advisory_with_no_published_fix():
    row = osv.parse_vuln(
        vuln(affected=[{"ranges": [{"events": [{"introduced": "0"}]}]}]),
        package_name="lodash",
    )
    assert row["fixed_version"] is None


def test_details_is_used_when_there_is_no_summary():
    row = osv.parse_vuln(
        vuln(summary=None, details="A longer description"), package_name="lodash"
    )
    assert row["summary"] == "A longer description"


def test_response_with_no_vulns_is_an_empty_list():
    """Most packages are clean; that is a result, not a failure."""
    assert osv.parse_response({}, package_name="react") == []
    assert osv.parse_response({"vulns": []}, package_name="react") == []


def test_non_dict_response_is_handled():
    assert osv.parse_response(None, package_name="react") == []
    assert osv.parse_response("nope", package_name="react") == []


def test_advisory_without_an_id_is_dropped():
    """Without an id it cannot be deduplicated, so it is unusable."""
    rows = osv.parse_response(
        {"vulns": [vuln(id=None), vuln()]}, package_name="lodash"
    )
    assert len(rows) == 1


def test_worst_severity_picks_the_highest():
    rows = [
        {"severity": "LOW"},
        {"severity": "CRITICAL"},
        {"severity": "MODERATE"},
    ]
    assert osv.worst_severity(rows) == "CRITICAL"


def test_worst_severity_is_none_when_nothing_is_graded():
    assert osv.worst_severity([{"severity": None}]) is None
    assert osv.worst_severity([]) is None


def test_query_payload_maps_the_ecosystem_name():
    """OSV spells it PyPI, we spell it pypi."""
    assert osv.query_payload("flask", "pypi")["package"]["ecosystem"] == "PyPI"
    assert osv.query_payload("express", "npm")["package"]["ecosystem"] == "npm"
