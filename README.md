# depwatch

Dependency health and supply-chain risk, as a pipeline.

POST a `package.json` and it tells you which of your dependencies carry
unfixed advisories, which are unmaintained, and which rest on a single
maintainer who last shipped years ago — with the reasoning for every score.

[![CI](https://github.com/Mudabbirarafat/depwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/Mudabbirarafat/depwatch/actions/workflows/ci.yml)

**Live: https://depwatch-2oj2.onrender.com** — paste a `package.json`, or load the
risky example. It runs on a free instance that sleeps when idle, so the first
request after a quiet spell takes about fifty seconds to wake it.

> **Status:** week four. npm metadata and OSV advisories ingest end to end,
> manifest ranges resolve to the version you would actually install, the API
> answers from the warehouse, there is a dashboard in front of it, and it is
> deployed with migrations, bounded endpoints and a logged ingestion path.
> PyPI, GitHub activity signals and dbt marts are next.

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
                          │
                          ▼
                     dashboard  ·  paste a package.json, get the report
```

## Quickstart

No services required — it defaults to SQLite.

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
depwatch migrate
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

The same three dependencies, pinned two different ways. Real registry and OSV
data; the only thing that changed is the manifest:

```
# "express": "^5.0.0", "lodash": "^4.17.21", "minimist": "^1.2.8"
  express    scored=5.3.0     score=0    low    5 past advisories, all fixed in the current version
  lodash     scored=4.18.1    score=15   low    single maintainer
  minimist   scored=1.2.8     score=25   low    no release in 3 years

# "express": "4.17.1", "lodash": "4.17.11", "minimist": "1.2.0"
  lodash     scored=4.17.11   score=85   HIGH   critical severity advisory GHSA-jf85-cpcp-j695
  minimist   scored=1.2.0     score=75   HIGH   critical severity advisory GHSA-xvch-5gv4-984h
  express    scored=4.17.1    score=25   low    moderate severity advisory GHSA-rv95-896h-c2vc
```

That difference is the whole point: a report about the latest release says
nothing useful to someone running an old pin.

Per-package detail:

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
only if it has no published fix, or if the fix landed after the version in
play. When a version cannot be parsed the advisory still counts: failing
towards "safe" on a security signal is the wrong way to be wrong.

**Manifest ranges resolve to a concrete version.** `^4.17.1` is resolved
against the versions the package has actually published, and the report says
which version it scored and how it got there (`resolved`, `latest`,
`unsupported-range`, `no-matching-version`). Prereleases are excluded, because
npm will not install `5.4.0-beta.1` for `^5.0.0` unless you ask for it by name.

**Ordering happens before limiting.** This one was a bug too. `/packages`
took the first fifty rows the database happened to return, scored those,
sorted them among themselves and presented the result as the worst packages in
the warehouse. At ten packages the right and wrong behaviours are identical,
which is why it survived three weeks. Sorting now covers every package in the
ecosystem and the limit only trims the response — so a page costs
O(packages) regardless of its size. That is the honest cost, and the reason a
materialised score column is the next step rather than a bigger page limit.

**The schema has a version.** `create_all()` built the tables until week four,
which works exactly once: the first change against a live database would have
been a manual `ALTER` in production. Alembic owns the schema now, the deployed
instance runs `upgrade head` before it opens its port, and the revisions live
*inside* the package so an installed wheel can find them — a migration
directory that does not ship is a migration that silently does nothing. Tests
still build their schema with `create_all()` because it is faster, and a drift
test compares the two so they cannot diverge quietly.

**The public endpoints are bounded.** `/analyse` accepts at most 200 packages,
counted after the manifest is merged rather than per field, and refuses a name
longer than the column. `/packages` caps its page size. Both sit behind a
per-client rate limit, which is in-process and therefore per-instance — a real
limitation, stated rather than hidden, and still better than an endpoint that
will score every package in the warehouse as fast as a loop can ask. `/healthz`
is exempt, because an instance that rate-limits its own health check looks
dead to the platform.

**The quiet failures are logged.** A quarantined record, a package that would
not fetch, a retry against a throttling registry: all three are non-fatal by
design, which is exactly why they need to be visible. The run returns
reasonable-looking counts and carries on, so the log is the only place the
detail exists. Output is plain text by default and JSON under
`LOG_FORMAT=json`, because a platform's log search is only useful when the
fields are fields.

**There are no hand-written indexes, and that is a finding rather than an
omission.** The unique constraints on `(ecosystem, name)` and
`(ecosystem, package_name, osv_id)` index exactly the columns every query
filters and orders by; `EXPLAIN QUERY PLAN` confirms all four hot queries use
them, and `/healthz` reads the newest run straight off the primary key without
a sort. Adding more would cost writes and buy nothing. A test asserts the plans
so that stays true.

**Scoring is a pure function.** No database, no network, no clock except the
one passed in. Every weight in `risk.py` is stated and directly tested, so you
can disagree with a specific rule rather than with a black box.

**Unknown packages are reported, not fetched inline.** A request that fans out
to npm and OSV would be slow, rate-limited and unpredictable. Ingestion is a
scheduled job; `"I have not ingested this yet"` is more honest than a timeout.

## Known limitations

Stated here rather than discovered by you:

- **The supported range grammar is a subset.** `^1.2.3`, `~1.2.3`, exact pins,
  `>=`/`>`/`<=`/`<`, `1.x` and `*` resolve. Unions (`^4 || ^5`), hyphen ranges,
  comparator sets and `workspace:`/`file:` specifiers do not: they are reported
  as `unsupported-range` and scored against the latest version, with the
  fallback stated in the report rather than hidden.
- **Severity comes from `database_specific.severity`**, which OSV only sets on
  GitHub-reviewed advisories. Ungraded advisories still count towards the
  "no published fix" rule but contribute no severity points.
- **Version comparison handles `MAJOR.MINOR.PATCH[-prerelease]`**, not the full
  semver grammar. Anything it cannot parse is treated as still vulnerable.
- **No GitHub signals yet** — last commit, contributor count and issue
  responsiveness are the strongest maintenance indicators and are not in here.

## Deployment

Running at **https://depwatch-2oj2.onrender.com**.

`render.yaml` is the deployment: a free Render web service, `pip install .`,
`uvicorn depwatch.api:app`, health-checked on `/healthz`. Standing up a copy
is "New > Blueprint" against a fork — there is no console state to reproduce.

Two things in there exist because of how a free instance actually behaves:

**It seeds itself on boot.** The filesystem is ephemeral and the instance
spins down when idle, so a cold start would otherwise serve an empty table.
Ten packages ingest in the background on startup, and the page says it is
still loading until they land. Seeding is *not* allowed to block startup:
waiting on npm and OSV before opening the port means the platform's first
health check times out and a working deploy is reported as a failed one.

**The schema is created before the port opens.** The same race from the other
side — `/healthz` counts rows, so the tables have to exist by the first health
check. That one is a single local round trip, so it is fine to block on.
Both cases are regression-tested in `tests/test_api.py`.

SQLite on instance-local disk is a deliberate choice over Render's free
Postgres, which expires after a month and leaves a dead link behind. The
warehouse is derived data and rebuilds in about a minute. Switching to
Postgres is a `databases:` block and `pip install ".[postgres]"`; no
application code changes.

## Tests

```bash
pytest       # 158 cases
ruff check .
```

Covering registry payload shapes (`repository` as string or object, `license`
as string, object or list, missing fields, unparseable timestamps), OSV
parsing, the retry policy (429, 5xx, connection errors, no retry on 404,
giving up at the limit), idempotent reruns, quarantine, raw landing, run
metadata, every scoring rule, version comparison, and the API end to end.

Also the operational behaviour, because that is where the bugs were: the
startup ordering both ways round, migrations against the models for drift,
the sliding rate-limit window, the query plans of every hot query, and that
the non-fatal failures actually reach the log.

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
  ranges.py        resolving a manifest range to an installed version
  api.py           /analyse, /packages, /healthz, the dashboard
  seed.py          filling a fresh instance, without blocking its boot
  migrate.py       upgrade head, from the CLI or from the booting server
  ratelimit.py     a sliding window per client; /healthz exempt
  logs.py          text by default, JSON for a platform to search
  cli.py           migrate, ingest, advisories, seed, runs, serve
  migrations/      alembic, shipped inside the package
  web/index.html   the dashboard, dependency-free
  sources/npm.py   registry projection and its awkward shapes
  sources/osv.py   advisory projection
```

## Next

1. Scheduled ingestion, so the warehouse refreshes on a timer rather than
   whenever the instance happens to restart
2. A materialised score, so `/packages` stops being O(packages) per request
3. PyPI source behind the same interface
4. GitHub activity: last commit, contributors, issue responsiveness
5. dbt marts with freshness and quality gates, failing the run on a breach
