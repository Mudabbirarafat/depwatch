# depwatch

Dependency health and supply-chain risk, as a pipeline.

Point it at a `package.json` and it tells you which of your dependencies are
unmaintained, which carry open advisories, and which rest on a single
maintainer who last shipped two years ago.

> **Status:** week one. npm ingestion runs end to end — fetch, land raw,
> validate, upsert, record the run. PyPI, GitHub activity, OSV advisories, the
> dbt marts and the dashboard are next.

## Pipeline

```
   registry APIs (npm, PyPI)          GitHub activity, OSV advisories
            │                                      │
            ▼                     (next)           ▼
   ┌──────────────────┐                   ┌──────────────────┐
   │ ingestion worker │                   │  enrichment      │
   │ rate limit +     │                   └──────────────────┘
   │ retry + backoff  │
   └────────┬─────────┘
            │  full payload, before any parsing
            ▼
     raw zone  data/raw/<source>/<date>/run-<id>.jsonl
            │
            ▼  validate (pydantic)
      ┌─────┴──────┐
      │            │ invalid
      ▼            ▼
  packages     quarantine          ingest_runs
  (upsert)     (reason+payload)    (what each run saw)
      │
      ▼  (next)
  dbt marts ──► FastAPI ──► dashboard
```

## Why it is built this way

**The raw payload lands before validation.** A parser bug should cost a replay
from disk, not a re-crawl of the registry. Registries rate limit; your own disk
does not.

**Writes are idempotent.** Ingestion is keyed on `(ecosystem, name)` and
upserts, so a rerun after a crash, a backfill, or a retried CI job leaves one
row rather than duplicates. `first_seen_at` is deliberately never overwritten.

**A bad record is quarantined, not fatal.** One malformed document out of five
hundred should not cost you the other four hundred and ninety-nine. Failures
land in `quarantine` with the reason and the payload.

**Every run is recorded.** `ingest_runs` stores what was requested, what
succeeded, what was quarantined and what failed. A pipeline you cannot inspect
after the fact is a black box, and freshness checks need something to read.

**Rate limits are respected.** Bounded concurrency, exponential backoff with
jitter, and `Retry-After` honoured when the server sends it. 404s are not
retried, because a missing package will not appear on the fourth ask.

## Quickstart

No services required — it defaults to SQLite.

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
depwatch init-db
depwatch ingest express react @types/node
depwatch runs
```

Against Postgres instead:

```bash
docker compose up
```

Read dependencies straight out of a manifest:

```bash
depwatch ingest --from-manifest ./package.json
```

## What a run looks like

```
$ depwatch ingest express react @types/node left-pad definitely-not-real
{
  "run_id": 1,
  "requested": 5,
  "succeeded": 4,
  "quarantined": 0,
  "failed": 1
}

$ depwatch runs
#1    npm    partial    requested=5    ok=4    quarantined=0   failed=1   2026-11-01 16:22
```

The missing package is counted, not fatal, and the run is marked `partial`
rather than `succeeded` — which is what a scheduled job should alert on.

## Tests

```bash
pytest
```

22 cases covering registry payload shapes (`repository` as string or object,
`license` as string, object or list, missing fields, unparseable timestamps),
the retry policy (429, 5xx, connection errors, no retry on 404, giving up after
the limit), and the ingestion loop (idempotent reruns, `first_seen_at`
surviving a refresh, quarantine, raw landing, run metadata).

CI additionally runs a live smoke ingest against the real registry, so a schema
change at npm fails the build instead of silently corrupting a mart.

## Layout

```
src/depwatch/
  config.py      settings from the environment
  models.py      packages, ingest_runs, quarantine
  db.py          engine, session, the idempotent upsert
  http.py        rate limiting, backoff, Retry-After
  ingest.py      fetch -> land raw -> validate -> upsert -> record
  cli.py         init-db, ingest, runs
  sources/npm.py registry projection and its awkward shapes
```

## Next

1. PyPI source behind the same interface
2. GitHub activity: last commit, contributor count, open issue ratio
3. OSV advisories joined per package and version
4. dbt marts and a risk score, with freshness and quality gates
5. FastAPI `POST /analyse` taking a manifest, plus the dashboard
6. Scheduled runs and alerting on a failed gate
