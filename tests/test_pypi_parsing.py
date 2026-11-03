"""PyPI payload shapes.

Each test here is a place the npm abstraction leaked when a second ecosystem
was plugged into it. Those are the interesting cases, so they are the ones
written down.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from depwatch.sources import pypi


@pytest.fixture
def document():
    """A realistic PyPI document, trimmed to the fields that are read."""
    return {
        "info": {
            "name": "requests",
            "version": "2.34.2",
            "summary": "Python HTTP for Humans.",
            "license": "Apache-2.0",
            "license_expression": None,
            "home_page": None,
            "project_urls": {
                "Documentation": "https://requests.readthedocs.io",
                "Source": "https://github.com/psf/requests",
            },
            "yanked": False,
            "classifiers": ["Development Status :: 5 - Production/Stable"],
        },
        "ownership": {
            "organization": None,
            "roles": [
                {"role": "Owner", "user": "Lukasa"},
                {"role": "Owner", "user": "graffatcolmingov"},
                {"role": "Owner", "user": "nateprewitt"},
            ],
        },
        "releases": {
            "2.34.1": [{"upload_time_iso_8601": "2026-04-01T10:00:00.000000Z"}],
            "2.34.2": [{"upload_time_iso_8601": "2026-05-14T19:25:26.443000Z"}],
        },
    }


def test_the_happy_path(document):
    package = pypi.parse(document)

    assert package.name == "requests"
    assert package.latest_version == "2.34.2"
    assert package.description == "Python HTTP for Humans."
    assert package.license == "Apache-2.0"
    assert package.repository_url == "https://github.com/psf/requests"
    assert package.maintainers == 3
    assert package.deprecated is False
    assert package.known_versions == ["2.34.1", "2.34.2"]
    assert package.last_published_at == datetime(2026, 5, 14, 19, 25, 26, 443000, tzinfo=UTC)


def test_maintainers_come_from_ownership_not_info(document):
    """npm puts maintainers on the document; PyPI puts them under `ownership`.

    This was the first place the shared abstraction leaked.
    """
    del document["ownership"]
    assert pypi.parse(document).maintainers is None


def test_a_single_maintainer_is_visible(document):
    document["ownership"]["roles"] = [{"role": "Owner", "user": "solo"}]
    assert pypi.parse(document).maintainers == 1


def test_an_spdx_expression_wins_over_the_legacy_field(document):
    document["info"]["license_expression"] = "MIT"
    document["info"]["license"] = "MIT License\n\nCopyright (c) ..."
    assert pypi.parse(document).license == "MIT"


def test_a_licence_that_is_actually_the_whole_licence_text_is_dropped(document):
    """A real and common shape. Truncating it to 128 characters would store
    something that reads like an identifier and is not."""
    document["info"]["license"] = "GNU GENERAL PUBLIC LICENSE\nVersion 3\n\n" + ("x" * 400)
    document["info"]["license_expression"] = None

    package = pypi.parse(document)
    assert package.license is None


def test_a_short_legacy_licence_is_kept(document):
    document["info"]["license"] = "BSD-3-Clause"
    assert pypi.parse(document).license == "BSD-3-Clause"


def test_a_yanked_latest_release_counts_as_deprecated(document):
    """PyPI has no deprecation flag. A yank is per release rather than per
    project, so this is an approximation - stated in the README, not hidden."""
    document["info"]["yanked"] = True
    assert pypi.parse(document).deprecated is True


def test_the_inactive_classifier_counts_as_deprecated(document):
    document["info"]["classifiers"] = ["Development Status :: 7 - Inactive"]
    assert pypi.parse(document).deprecated is True


def test_releases_with_no_files_are_not_installable(document):
    """PyPI keeps entries for releases whose files were deleted. Including them
    would let a range resolve to a version that cannot be installed."""
    document["releases"]["2.33.0"] = []

    package = pypi.parse(document)
    assert "2.33.0" not in package.known_versions
    assert package.known_versions == ["2.34.1", "2.34.2"]


def test_repository_url_falls_back_through_the_key_variants(document):
    document["info"]["project_urls"] = {"Repository": "https://example.invalid/r"}
    assert pypi.parse(document).repository_url == "https://example.invalid/r"

    document["info"]["project_urls"] = {}
    document["info"]["home_page"] = "https://example.invalid/home"
    assert pypi.parse(document).repository_url == "https://example.invalid/home"

    document["info"]["home_page"] = None
    assert pypi.parse(document).repository_url is None


def test_a_document_with_no_name_is_rejected(document):
    """Quarantined by the ingest loop rather than failing the run."""
    document["info"]["name"] = ""
    with pytest.raises(ValidationError):
        pypi.parse(document)


def test_missing_sections_do_not_crash():
    """The whole document can be far thinner than the happy path."""
    package = pypi.parse({"info": {"name": "minimal", "version": "1.0.0"}})

    assert package.name == "minimal"
    assert package.latest_version == "1.0.0"
    assert package.known_versions == []
    assert package.last_published_at is None
    assert package.maintainers is None
    assert package.license is None


def test_an_unparseable_upload_time_is_dropped_not_fatal(document):
    document["releases"]["2.34.2"] = [{"upload_time_iso_8601": "not-a-timestamp"}]
    assert pypi.parse(document).last_published_at is None


def test_the_older_upload_time_field_is_accepted(document):
    document["releases"]["2.34.2"] = [{"upload_time": "2026-05-14T19:25:26"}]
    assert pypi.parse(document).last_published_at is not None


def test_the_newest_file_of_the_latest_release_wins(document):
    """A release has several files - sdist and wheels - uploaded minutes apart."""
    document["releases"]["2.34.2"] = [
        {"upload_time_iso_8601": "2026-05-14T19:25:26.000000Z"},
        {"upload_time_iso_8601": "2026-05-14T19:31:02.000000Z"},
    ]
    assert pypi.parse(document).last_published_at.minute == 31


def test_the_row_carries_the_pypi_ecosystem(document):
    row = pypi.parse(document).to_row()
    assert row["ecosystem"] == "pypi"
    assert row["name"] == "requests"


def test_the_package_url_is_the_json_endpoint():
    assert pypi.package_url("https://pypi.org", "requests") == "https://pypi.org/pypi/requests/json"
    assert pypi.package_url("https://pypi.org/", "flask") == "https://pypi.org/pypi/flask/json"


def test_an_organisation_owned_project_is_not_single_maintainer(document):
    """Django's ownership is {"organization": "django", "roles": []}. Counting
    that as zero maintainers made the scorer rate one of the best-maintained
    packages in Python as single-maintainer and high risk."""
    document["ownership"] = {"organization": "django", "roles": []}

    assert pypi.parse(document).maintainers is None


def test_an_empty_roles_list_is_unknown_not_zero(document):
    """A published package always has someone able to publish it, so zero
    never means "nobody" - it means PyPI did not say."""
    document["ownership"] = {"organization": None, "roles": []}

    assert pypi.parse(document).maintainers is None


def test_a_normalised_name_is_stored(document):
    """PyPI answers with the canonical display name, not what was asked for."""
    document["info"]["name"] = "PyYAML"
    assert pypi.parse(document).name == "pyyaml"

    document["info"]["name"] = "zope.interface"
    assert pypi.parse(document).name == "zope-interface"

    document["info"]["name"] = "ruamel_yaml"
    assert pypi.parse(document).name == "ruamel-yaml"


def test_normalise_matches_pep_503():
    assert pypi.normalise("Django") == "django"
    assert pypi.normalise("A.B__C--D") == "a-b-c-d"
    assert pypi.normalise("  spaced  ") == "spaced"
