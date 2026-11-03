"""The ingestion loop: fetch, land raw, validate, upsert, record the run.

Order matters. The raw payload is written to disk *before* validation, so a
parser bug is always recoverable by replaying the raw zone rather than
re-crawling the registry. Validation failures are quarantined with a reason and
the run continues; a single malformed package must not cost you the other 499.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from depwatch.config import settings
from depwatch.db import session_scope, upsert_advisories, upsert_packages
from depwatch.http import RateLimitedClient
from depwatch.models import IngestRun, Quarantine
from depwatch.sources import normalise_name, npm, osv, pypi, source_for

log = logging.getLogger("depwatch.ingest")

def registry_for(ecosystem: str) -> str:
    return {
        npm.ECOSYSTEM: settings.npm_registry,
        pypi.ECOSYSTEM: settings.pypi_registry,
    }[ecosystem]


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


async def fetch_all(
    client: RateLimitedClient, registry: str, names: list[str], ecosystem: str = "npm"
) -> dict:
    """Fetch every package concurrently; keep failures instead of raising."""
    source = source_for(ecosystem)
    results: dict[str, dict | Exception] = {}

    async def one(name: str) -> None:
        try:
            results[name] = await client.get_json(source.package_url(registry, name))
        except Exception as exc:  # noqa: BLE001 - recorded on the run, not swallowed
            results[name] = exc

    await asyncio.gather(*(one(n) for n in names))
    return results


async def ingest_packages(
    names: list[str],
    *,
    ecosystem: str = npm.ECOSYSTEM,
    client: RateLimitedClient | None = None,
    db_url: str | None = None,
    raw_dir: Path | None = None,
) -> dict:
    """Ingest a list of packages from one ecosystem. Returns a run summary.

    The body is ecosystem-agnostic: everything registry-specific lives in the
    source module, which is the point. The only thing that changed when PyPI
    was added was the lookup at the top.
    """
    source = source_for(ecosystem)
    owns_client = client is None
    client = client or RateLimitedClient()
    root = Path(raw_dir or settings.raw_dir)

    with session_scope(db_url) as session:
        run = IngestRun(source=ecosystem, requested=len(names))
        session.add(run)
        session.flush()  # assigns run.id
        run_id = run.id

    log.info(
        "ingest run started",
        extra={"run_id": run_id, "source": ecosystem, "requested": len(names)},
    )

    try:
        fetched = await fetch_all(client, registry_for(ecosystem), names, ecosystem)
    finally:
        if owns_client:
            await client.aclose()

    documents = {n: d for n, d in fetched.items() if isinstance(d, dict)}
    failures = {n: e for n, e in fetched.items() if not isinstance(e, dict)}

    write_raw(raw_path(root, ecosystem, run_id), list(documents.values()))

    for name, error in failures.items():
        log.warning(
            "fetch failed",
            extra={"run_id": run_id, "source": ecosystem, "package": name,
                   "error": str(error)[:200]},
        )

    rows, bad = [], []
    for name, document in documents.items():
        try:
            rows.append(source.parse(document).to_row())
        except ValidationError as exc:
            bad.append((name, str(exc), document))
            log.warning(
                "record quarantined",
                extra={"run_id": run_id, "source": ecosystem, "package": name,
                       "reason": str(exc)[:200]},
            )

    with session_scope(db_url) as session:
        written = upsert_packages(session, rows)
        for name, reason, payload in bad:
            session.add(
                Quarantine(
                    run_id=run_id,
                    source=ecosystem,
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

    summary = {
        "run_id": run_id,
        "requested": len(names),
        "succeeded": len(rows),
        "quarantined": len(bad),
        "failed": len(failures),
    }
    log.info("ingest run finished", extra={"source": ecosystem, **summary})
    return summary


async def ingest_npm(names: list[str], **kwargs) -> dict:
    """The npm case of `ingest_packages`. Kept because it reads better at call
    sites that only ever mean npm, and because the CLI and seed set predate the
    second ecosystem."""
    return await ingest_packages(names, ecosystem=npm.ECOSYSTEM, **kwargs)


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

    log.info(
        "ingest run started",
        extra={"run_id": run_id, "source": "osv", "requested": len(names)},
    )

    # Normalised before the query, so the advisory rows are keyed the same way
    # the package rows are. Getting this wrong meant PyPI advisories were stored
    # under `django` while the package sat under `Django`, and the two never
    # joined - see sources/__init__.py.
    names = [normalise_name(ecosystem, n) for n in names]

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

    for name, error in failures.items():
        log.warning(
            "advisory query failed",
            extra={"run_id": run_id, "source": "osv", "package": name,
                   "error": str(error)[:200]},
        )

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

    summary = {
        "run_id": run_id,
        "requested": len(names),
        "advisories": written,
        "packages_clean": sum(1 for n in bodies if not any(r["package_name"] == n for r in rows)),
        "failed": len(failures),
    }
    log.info("ingest run finished", extra={"source": "osv", **summary})
    return summary
