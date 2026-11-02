"""The ingestion loop: fetch, land raw, validate, upsert, record the run.

Order matters. The raw payload is written to disk *before* validation, so a
parser bug is always recoverable by replaying the raw zone rather than
re-crawling the registry. Validation failures are quarantined with a reason and
the run continues; a single malformed package must not cost you the other 499.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from depwatch.config import settings
from depwatch.db import session_scope, upsert_advisories, upsert_packages
from depwatch.http import RateLimitedClient
from depwatch.models import IngestRun, Quarantine
from depwatch.sources import npm, osv


def raw_path(root: Path, source: str, run_id: int, when: datetime | None = None) -> Path:
    day = (when or datetime.now(UTC)).strftime("%Y-%m-%d")
    return Path(root) / source / day / f"run-{run_id}.jsonl"


def write_raw(path: Path, records: list[dict]) -> Path:
    """Land payloads as JSONL. One file per run, partitioned by day."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


async def fetch_all(client: RateLimitedClient, registry: str, names: list[str]) -> dict:
    """Fetch every package concurrently; keep failures instead of raising."""
    results: dict[str, dict | Exception] = {}

    async def one(name: str) -> None:
        try:
            results[name] = await client.get_json(npm.package_url(registry, name))
        except Exception as exc:  # noqa: BLE001 - recorded on the run, not swallowed
            results[name] = exc

    await asyncio.gather(*(one(n) for n in names))
    return results


async def ingest_npm(
    names: list[str],
    *,
    client: RateLimitedClient | None = None,
    db_url: str | None = None,
    raw_dir: Path | None = None,
) -> dict:
    """Ingest a list of npm packages. Returns a summary of the run."""
    owns_client = client is None
    client = client or RateLimitedClient()
    root = Path(raw_dir or settings.raw_dir)

    with session_scope(db_url) as session:
        run = IngestRun(source=npm.ECOSYSTEM, requested=len(names))
        session.add(run)
        session.flush()  # assigns run.id
        run_id = run.id

    try:
        fetched = await fetch_all(client, settings.npm_registry, names)
    finally:
        if owns_client:
            await client.aclose()

    documents = {n: d for n, d in fetched.items() if isinstance(d, dict)}
    failures = {n: e for n, e in fetched.items() if not isinstance(e, dict)}

    write_raw(raw_path(root, npm.ECOSYSTEM, run_id), list(documents.values()))

    rows, bad = [], []
    for name, document in documents.items():
        try:
            rows.append(npm.parse(document).to_row())
        except ValidationError as exc:
            bad.append((name, str(exc), document))

    with session_scope(db_url) as session:
        written = upsert_packages(session, rows)
        for name, reason, payload in bad:
            session.add(
                Quarantine(
                    run_id=run_id,
                    source=npm.ECOSYSTEM,
                    key=name,
                    reason=reason,
                    payload=payload,
                )
            )
        run = session.get(IngestRun, run_id)
        run.succeeded = written
        run.quarantined = len(bad)
        run.failed = len(failures)
        run.finished_at = datetime.now(UTC)
        run.status = "succeeded" if not failures else "partial"
        if failures:
            run.error = "; ".join(f"{n}: {e}" for n, e in list(failures.items())[:5])

    return {
        "run_id": run_id,
        "requested": len(names),
        "succeeded": len(rows),
        "quarantined": len(bad),
        "failed": len(failures),
    }


async def ingest_advisories(
    names: list[str],
    *,
    client: RateLimitedClient | None = None,
    db_url: str | None = None,
    raw_dir: Path | None = None,
    ecosystem: str = "npm",
) -> dict:
    """Pull advisories from OSV for the given packages.

    OSV answers per package, so a package with no advisories is a legitimate
    empty result rather than a failure - most packages are clean, and treating
    that as an error would make every run look broken.
    """
    owns_client = client is None
    client = client or RateLimitedClient()
    root = Path(raw_dir or settings.raw_dir)

    with session_scope(db_url) as session:
        run = IngestRun(source="osv", requested=len(names))
        session.add(run)
        session.flush()
        run_id = run.id

    responses: dict[str, object] = {}
    try:
        for name in names:
            try:
                responses[name] = await client.post_json(
                    settings.osv_api, osv.query_payload(name, ecosystem)
                )
            except Exception as exc:  # noqa: BLE001 - recorded on the run
                responses[name] = exc
    finally:
        if owns_client:
            await client.aclose()

    bodies = {n: b for n, b in responses.items() if not isinstance(b, Exception)}
    failures = {n: b for n, b in responses.items() if isinstance(b, Exception)}

    write_raw(raw_path(root, "osv", run_id), [{"package": n, "response": b} for n, b in bodies.items()])

    rows: list[dict] = []
    for name, body in bodies.items():
        rows.extend(osv.parse_response(body, package_name=name, ecosystem=ecosystem))

    with session_scope(db_url) as session:
        written = upsert_advisories(session, rows)
        run = session.get(IngestRun, run_id)
        run.succeeded = written
        run.failed = len(failures)
        run.finished_at = datetime.now(UTC)
        run.status = "succeeded" if not failures else "partial"
        if failures:
            run.error = "; ".join(f"{n}: {e}" for n, e in list(failures.items())[:5])

    return {
        "run_id": run_id,
        "requested": len(names),
        "advisories": written,
        "packages_clean": sum(1 for n in bodies if not any(r["package_name"] == n for r in rows)),
        "failed": len(failures),
    }
