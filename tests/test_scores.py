"""Materialising the score.

The interesting cases are the ones where a derived column can go wrong: an
ingest blanking it, one ecosystem borrowing another's advisories, and the
staleness that storing anything derived invites.
"""

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pytest
from sqlalchemy import select

from depwatch.db import init_db, session_scope, upsert_packages
from depwatch.gates import evaluate
from depwatch.models import Advisory, Package
from depwatch.scores import rescore


@pytest.fixture
def db(tmp_path):
    init_db(f"sqlite:///{tmp_path / 'scores.db'}")


def add_package(name="lodash", ecosystem="npm", **kwargs):
    defaults = {
        "latest_version": "1.0.0",
        "license": "MIT",
        "maintainers": 4,
        "deprecated": False,
        "last_published_at": datetime.now(UTC) - timedelta(days=5),
    }
    with session_scope() as session:
        session.add(Package(ecosystem=ecosystem, name=name, **{**defaults, **kwargs}))


def add_advisory(package_name="lodash", ecosystem="npm", osv_id="GHSA-1", **kwargs):
    with session_scope() as session:
        session.add(
            Advisory(
                ecosystem=ecosystem,
                package_name=package_name,
                osv_id=osv_id,
                severity=kwargs.pop("severity", "CRITICAL"),
                fixed_version=kwargs.pop("fixed_version", None),
                **kwargs,
            )
        )


class Stored(NamedTuple):
    """The values, read inside the session.

    Returning the ORM object instead detaches it when the session closes, and
    every attribute access then raises.
    """

    score: int | None
    band: str | None
    reasons: list
    scored_at: object
    latest_version: str | None


def stored(name="lodash", ecosystem="npm") -> Stored:
    with session_scope() as session:
        package = session.scalars(
            select(Package).where(Package.ecosystem == ecosystem, Package.name == name)
        ).one()
        return Stored(
            score=package.score,
            band=package.band,
            reasons=list(package.reasons or []),
            scored_at=package.scored_at,
            latest_version=package.latest_version,
        )


def test_rescore_writes_the_score_band_and_reasons(db):
    add_package(deprecated=True, maintainers=1)
    summary = rescore("npm")

    package = stored()
    assert summary["scored"] == 1
    assert package.score > 0
    assert package.band in {"low", "medium", "high"}
    assert any("deprecated" in r for r in package.reasons)
    assert package.scored_at is not None


def test_advisories_are_matched_within_their_own_ecosystem(db):
    """`express` exists on npm and on PyPI. One must not borrow the other's
    advisories - the same mismatch that the PyPI name normalisation fixed, from
    the other direction."""
    add_package(name="express", ecosystem="npm")
    add_package(name="express", ecosystem="pypi")
    add_advisory(package_name="express", ecosystem="npm", severity="CRITICAL")

    rescore()

    assert stored("express", "npm").score > 0
    assert stored("express", "pypi").score == 0


def test_an_ingest_does_not_blank_the_stored_score(db):
    """The trap in storing a derived column on the same row.

    `upsert_packages` updates every non-key column from the incoming row, and
    an ingest row carries no score - so without excluding these columns the
    upsert would quietly reset every score to NULL on each run.
    """
    add_package(deprecated=True)
    rescore("npm")
    before = stored().score
    assert before > 0

    with session_scope() as session:
        upsert_packages(
            session,
            [
                {
                    "ecosystem": "npm",
                    "name": "lodash",
                    "latest_version": "1.0.1",
                    "deprecated": True,
                    "fetched_at": datetime.now(UTC),
                    "first_seen_at": datetime.now(UTC),
                }
            ],
        )

    after = stored()
    assert after.latest_version == "1.0.1", "the ingest should still refresh the data"
    assert after.score == before, "but it must not blank the derived score"


def test_rescore_is_idempotent(db):
    add_package(deprecated=True)
    first = rescore("npm")
    score_after_first = stored().score
    second = rescore("npm")

    assert first["scored"] == second["scored"] == 1
    assert stored().score == score_after_first


def test_rescore_scopes_itself_to_one_ecosystem(db):
    add_package(name="a", ecosystem="npm", deprecated=True)
    add_package(name="b", ecosystem="pypi", deprecated=True)

    rescore("pypi")

    assert stored("a", "npm").score is None
    assert stored("b", "pypi").score is not None


def test_rescore_on_an_empty_warehouse_does_nothing(db):
    assert rescore("npm")["scored"] == 0


def test_an_unscored_package_fails_its_gate(db):
    """Nothing else would ever mention a score that was never computed."""
    add_package()
    add_advisory()

    report = evaluate()
    gate = next(g for g in report["gates"] if g["name"] == "packages_are_scored")
    assert gate["passed"] is False
    assert "1 of 1" in gate["detail"]

    rescore("npm")
    report = evaluate()
    gate = next(g for g in report["gates"] if g["name"] == "packages_are_scored")
    assert gate["passed"] is True
