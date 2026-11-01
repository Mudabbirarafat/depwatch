"""Parsing registry documents.

Registries are inconsistent, so the parser has to absorb several shapes for the
same field rather than assuming the happy path.
"""

import pytest
from pydantic import ValidationError

from depwatch.sources import npm


def test_parses_a_normal_document(registry_doc):
    package = npm.parse(registry_doc)
    assert package.name == "express"
    assert package.latest_version == "4.19.2"
    assert package.license == "MIT"
    assert package.maintainers == 2
    assert package.repository_url.endswith("expressjs/express.git")
    assert package.last_published_at.year == 2024
    assert package.deprecated is False


def test_repository_may_be_a_plain_string(registry_doc):
    registry_doc["repository"] = "https://github.com/expressjs/express"
    assert npm.parse(registry_doc).repository_url.endswith("expressjs/express")


def test_license_may_be_the_legacy_object(registry_doc):
    registry_doc["license"] = {"type": "Apache-2.0", "url": "..."}
    assert npm.parse(registry_doc).license == "Apache-2.0"


def test_license_may_be_a_list(registry_doc):
    registry_doc["license"] = [{"type": "ISC"}]
    assert npm.parse(registry_doc).license == "ISC"


def test_missing_optional_fields_are_tolerated():
    package = npm.parse({"name": "tiny", "dist-tags": {}, "versions": {}})
    assert package.name == "tiny"
    assert package.latest_version is None
    assert package.maintainers is None
    assert package.last_published_at is None


def test_deprecated_flag_is_read_from_the_latest_version(registry_doc):
    registry_doc["versions"]["4.19.2"]["deprecated"] = "use something else"
    assert npm.parse(registry_doc).deprecated is True


def test_unparseable_timestamp_does_not_raise(registry_doc):
    registry_doc["time"] = {"modified": "not-a-date", "4.19.2": "also-not"}
    assert npm.parse(registry_doc).last_published_at is None


def test_document_without_a_name_is_rejected():
    """The ingest loop turns this into a quarantined record."""
    with pytest.raises(ValidationError):
        npm.parse({"dist-tags": {"latest": "1.0.0"}})


def test_scoped_package_url_is_encoded():
    url = npm.package_url("https://registry.npmjs.org", "@types/node")
    assert url == "https://registry.npmjs.org/@types%2fnode"
