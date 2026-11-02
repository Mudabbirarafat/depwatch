# depwatch

Dependency health and supply-chain risk, as a pipeline.

POST a `package.json` and it tells you which of your dependencies carry
unfixed advisories, which are unmaintained, and which rest on a single
maintainer who last shipped years ago — with the reasoning for every score.

[![CI](https://github.com/Mudabbirarafat/depwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/Mudabbirarafat/depwatch/actions/workflows/ci.yml)

> **Status:** week two. npm metadata and OSV advisories ingest end to end,
> risk scoring is version-aware, and the API answers from the warehouse.
> PyPI, GitHub activity signals, dbt marts and the dashboard are next.

## Pipeline

```
   registry.npmjs.org            api.osv.dev/v1/query
            │                             │
            ▼                             ▼
   ┌─────────────────────────────────────────────┐
   │  ingestion  ·  bounded concurrency,         │
   │  exponential backoff, Retry-After honoured  │
   └──────────────────────┬──────────────────────┘
                          │  full payload, before any parsing
                          ▼
            raw zone   data/raw/<source>/<date>/run-<id>.jsonl
                          │
                          ▼  validate (pydantic)
                   ┌──────┴───────┐
                   │              │ invalid
                   ▼              ▼
          packages, advisories  quarantine        ingest_runs
             (idempotent upsert)  (reason+payload)  (what each run saw)
                   │
                   ▼  risk scoring (pure, version-aware)
             FastAPI  ──  POST /analyse · GET /packages · GET /healthz
```

## Quickstart

No services required — it defaults to SQLite.

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
depwatch init-db
depwatch ingest lodash express react minimist
depwatch advisories lodash express react minimist
depwatch serve
```

Then:

```bash
curl -s localhost:8000/analyse -H 'Content-Type: application/json' -d '{"names":["lodash","express","minimist"]}'
```

Against Postgres instead: `docker compose up`.

## What it actually returns

Real output, from real registry and OSV data:

```
summary: {"requested": 4, "analysed": 4, "unknown": 0, "high": 0, "medium": 0, "low": 4}

  minimist   score=25   low     advisories=2
      - no release in 3 years
      - 2 past advisories, all fixed in the current version
  lodash     score=15   low     advisories=10
      - single maintainer
      - 10 past advisories, all fixed in the current version
  express    score=0    low     advisories=5
      - 5 past advisories, all fixed in the current version
```

Every score comes with its reasons. A number without an explanation is not
useful to anyone deciding whether to adopt a package.

## Why it is built this way

**The raw payload lands before validation.** A parser bug should cost a replay
from disk, not a re-crawl. Registries rate limit; your own disk does not.

**Writes are idempotent.** Packages are keyed on `(ecosystem, name)` and
advisories on `(ecosystem, package_name, osv_id)`, both upserting. Rerun after
a crash, a backfill or a retried CI job and you get one row, refreshed.
`first_seen_at` is never overwritten.

**A bad record is quarantined, not fatal.** One malformed document out of five
hundred should not cost you the other four hundred and ninety-nine. Failures
land in `quarantine` with the reason and the payload.

**Every run is recorded.** `ingest_runs` stores requested, succeeded,
quarantined and failed. A pipeline you cannot inspect after the fact is a black
box, and freshness checks need something to read.

**Scoring is version-aware.** This one was a bug first. Counting every advisory
ever published for a package rated `express` as medium risk — when all five of
its advisories were fixed by 4.20.0 and it ships 5.3.0. An advisory now counts
only if it has no published fix, or if the fix landed after the current
version. When a version cannot be parsed the advisory still counts: failing
towards "safe" on a security signal is the wrong way to be wrong.

**Scoring is a pure function.** No database, no network, no clock except the
one passed in. Every weight in `risk.py` is stated and directly tested, so you
can disagree with a specific rule rather than with a black box.

**Unknown packages are reported, not fetched inline.** A request that fans out
to npm and OSV would be slow, rate-limited and unpredictable. Ingestion is a
scheduled job; `"I have not ingested this yet"` is more honest than a timeout.

## Known limitations

Stated here rather than discovered by you:

- **Advisories are matched against the package's latest version, not yours.**
  A manifest range like `^4.17.0` is not resolved, so a report can understate
  risk for a project pinned to an older version. Resolving ranges is the next
  correctness step.
- **Severity comes from `database_specific.severity`**, which OSV only sets on
  GitHub-reviewed advisories. Ungraded advisories still count towards the
  "no published fix" rule but contribute no severity points.
- **Version comparison handles `MAJOR.MINOR.PATCH[-prerelease]`**, not the full
  semver grammar. Anything it cannot parse is treated as still vulnerable.
- **No GitHub signals yet** — last commit, contributor count and issue
  responsiveness are the strongest maintenance indicators and are not in here.

## Tests

```bash
pytest       # 81 cases
ruff check .
```

Covering registry payload shapes (`repository` as string or object, `license`
as string, object or list, missing fields, unparseable timestamps), OSV
parsing, the retry policy (429, 5xx, connection errors, no retry on 404,
giving up at the limit), idempotent reruns, quarantine, raw landing, run
metadata, every scoring rule, version comparison, and the API end to end.

CI additionally runs a live ingest against the real npm registry and OSV, so a
schema change at either fails the build instead of silently corrupting a mart.

## Layout

```
src/depwatch/
  config.py        settings from the environment
  models.py        packages, advisories, ingest_runs, quarantine
  db.py            engine, session, idempotent upserts
  http.py          rate limiting, backoff, Retry-After; GET and POST
  ingest.py        fetch -> land raw -> validate -> upsert -> record
  risk.py          scoring: pure, weighted, explained
  versions.py      just enough semver to ask "is this still vulnerable?"
  api.py           /analyse, /packages, /healthz
  cli.py           init-db, ingest, advisories, runs, serve
  sources/npm.py   registry projection and its awkward shapes
  sources/osv.py   advisory projection
```

## Next

1. Resolve manifest version ranges so reports reflect your pinned versions
2. PyPI source behind the same interface
3. GitHub activity: last commit, contributors, issue responsiveness
4. dbt marts with freshness and quality gates
5. Dashboard, deployed
6. Scheduled runs with alerting on a failed gate
