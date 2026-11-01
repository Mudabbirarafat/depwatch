"""The ingestion loop: idempotency, quarantine, raw landing, run metadata."""

import json

import httpx
from sqlalchemy import select

from depwatch.db import session_scope
from depwatch.ingest import ingest_npm
from depwatch.models import IngestRun, Package, Quarantine


def responder(docs: dict, status: int = 200):
    """Serve a registry document per package name."""

    def handler(request: httpx.Request) -> httpx.Response:
        name = str(request.url).rsplit("/", 1)[-1].replace("%2f", "/")
        if name not in docs:
            return httpx.Response(404, text="not found")
        return httpx.Response(status, json=docs[name])

    return handler


async def test_ingest_writes_a_package(db_url, tmp_path, registry_doc, mock_client):
    summary = await ingest_npm(
        ["express"],
        client=mock_client(responder({"express": registry_doc})),
        db_url=db_url,
        raw_dir=tmp_path / "raw",
    )
    assert summary["succeeded"] == 1

    with session_scope(db_url) as session:
        package = session.scalars(select(Package)).one()
        assert package.name == "express"
        assert package.license == "MIT"
        assert package.maintainers == 2


async def test_rerun_updates_rather_than_duplicates(db_url, tmp_path, registry_doc, mock_client):
    """Idempotency: the same run twice must leave one row, refreshed."""
    client_args = {"db_url": db_url, "raw_dir": tmp_path / "raw"}

    await ingest_npm(
        ["express"], client=mock_client(responder({"express": registry_doc})), **client_args
    )

    bumped = {**registry_doc, "dist-tags": {"latest": "5.0.0"}}
    bumped["versions"] = {"5.0.0": {"license": "MIT"}}
    await ingest_npm(
        ["express"], client=mock_client(responder({"express": bumped})), **client_args
    )

    with session_scope(db_url) as session:
        packages = session.scalars(select(Package)).all()
        assert len(packages) == 1
        assert packages[0].latest_version == "5.0.0"


async def test_first_seen_survives_a_refresh(db_url, tmp_path, registry_doc, mock_client):
    client_args = {"db_url": db_url, "raw_dir": tmp_path / "raw"}
    await ingest_npm(
        ["express"], client=mock_client(responder({"express": registry_doc})), **client_args
    )
    with session_scope(db_url) as session:
        original = session.scalars(select(Package)).one().first_seen_at

    await ingest_npm(
        ["express"], client=mock_client(responder({"express": registry_doc})), **client_args
    )
    with session_scope(db_url) as session:
        assert session.scalars(select(Package)).one().first_seen_at == original


async def test_invalid_document_is_quarantined_and_run_continues(
    db_url, tmp_path, registry_doc, mock_client
):
    """One bad package must not cost the good ones."""
    docs = {"express": registry_doc, "broken": {"dist-tags": {"latest": "1.0.0"}}}

    summary = await ingest_npm(
        ["express", "broken"],
        client=mock_client(responder(docs)),
        db_url=db_url,
        raw_dir=tmp_path / "raw",
    )
    assert summary["succeeded"] == 1
    assert summary["quarantined"] == 1

    with session_scope(db_url) as session:
        bad = session.scalars(select(Quarantine)).one()
        assert bad.key == "broken"
        assert "name" in bad.reason
        assert bad.payload["dist-tags"]["latest"] == "1.0.0"


async def test_missing_package_is_counted_as_failed_not_fatal(db_url, tmp_path, registry_doc, mock_client):
    summary = await ingest_npm(
        ["express", "does-not-exist"],
        client=mock_client(responder({"express": registry_doc})),
        db_url=db_url,
        raw_dir=tmp_path / "raw",
    )
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1

    with session_scope(db_url) as session:
        run = session.scalars(select(IngestRun)).one()
        assert run.status == "partial"
        assert "does-not-exist" in run.error


async def test_raw_payload_is_landed_before_parsing(db_url, tmp_path, registry_doc, mock_client):
    """The raw zone is what makes a parser bug recoverable without re-crawling."""
    raw_dir = tmp_path / "raw"
    await ingest_npm(
        ["express"],
        client=mock_client(responder({"express": registry_doc})),
        db_url=db_url,
        raw_dir=raw_dir,
    )

    files = list(raw_dir.rglob("*.jsonl"))
    assert len(files) == 1
    landed = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert landed[0]["name"] == "express"
    # the whole document, not our projection
    assert "versions" in landed[0]


async def test_run_metadata_is_recorded(db_url, tmp_path, registry_doc, mock_client):
    await ingest_npm(
        ["express"],
        client=mock_client(responder({"express": registry_doc})),
        db_url=db_url,
        raw_dir=tmp_path / "raw",
    )
    with session_scope(db_url) as session:
        run = session.scalars(select(IngestRun)).one()
        assert run.status == "succeeded"
        assert run.requested == 1
        assert run.succeeded == 1
        assert run.finished_at is not None
