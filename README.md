# depwatch

Dependency health and supply-chain risk, as a pipeline.

POST a `package.json` and it tells you which of your dependencies carry
unfixed advisories, which are unmaintained, and which rest on a single
maintainer who last shipped years ago — with the reasoning for every score.

[![CI](https://github.com/Mudabbirarafat/depwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/Mudabbirarafat/depwatch/actions/workflows/ci.yml)

**Live: https://depwatch-2oj2.onrender.com** — paste a `package.json`, or load the
risky example. It runs on a free instance that sleeps when idle, so the first
request after a quiet spell takes about fifty seconds to wake it.

> **Status:** week five. npm metadata and OSV advisories ingest end to end,
> manifest ranges resolve to the version you would actually install, the API
> answers from the warehouse, there is a dashboard in front of it, and it is
> deployed with migrations, bounded endpoints and a logged ingestion path,
> and it refreshes on a schedule behind freshness and quality gates. PyPI,
> GitHub activity signals and dbt marts are next.

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


   scheduled workflow  ──  POST /refresh  ──▶  the loop above runs again
            │                                            │
            └──────────  GET /gates  ◀───────────────────┘
                            │
                            ▼  a breach fails the workflow, which is the alert
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
- **The rate limit is per instance.** It lives in the process, so two instances
  mean two independent allowances and a restart forgets every count. A shared
  limiter needs Redis, which is not worth a dependency at this size.
- **Deployed on SQLite, not Postgres.** Render's free Postgres expires after a
  month and leaves a dead link behind, which is the failure this project was
  partly built to avoid. CI proves the Postgres path on every push; switching
  is a `databases:` block and `pip install ".[postgres]"`.

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

## Scheduling and gates

Ingestion used to be a thing you ran. It is now a thing that happens.

A scheduled GitHub Actions workflow calls `POST /refresh` on the deployed
instance once a day, waits for it, then checks `GET /gates`. Render's free plan
has no cron, so the schedule lives in the workflow — which is not purely a
workaround: a schedule in the repository is reviewable in a diff, and a
schedule in a console is a setting somebody has to remember to look at.

**The gates read the run metadata that nothing was reading.** Every run already
recorded what it saw; without something checking those numbers they were
decoration, because a pipeline that quietly stops ingesting looks exactly like
a healthy one from outside. Five gates: the warehouse has data, advisories were
actually fetched, the newest finished run is recent enough, and the last run's
failure and quarantine rates are under their thresholds.

They are deliberately about the data, not the process. "The server is up" is
`/healthz`. "The server is up and serving three-week-old numbers from one
successful run in nine attempts" is what these catch — and `/healthz` stays
green through a gate breach on purpose, because the platform restarts an
instance whose health check fails and stale data is not a reason to restart a
working server.

**A failing gate is the alerting.** `/gates` answers 503 rather than
200-with-a-flag, so a caller checking only the status code cannot mistake bad
data for good; the full report is in the body either way, because a monitor
needs to know which gate failed. The workflow exits non-zero on a breach, and
GitHub emails about failed scheduled workflows.

**`/refresh` is the one endpoint that needs a secret.** It starts a crawl
against two external APIs and rewrites the warehouse, so an open one is a way
to spend someone else's rate limit. One shared token, compared with
`compare_digest`, and it **fails closed** — an unset `REFRESH_TOKEN` makes the
endpoint unavailable rather than unprotected, because the convenient reading is
how a half-finished deploy becomes an open write endpoint. A second refresh
while one is running is declined rather than queued: overlapping crawls of the
same packages double the rate-limit pressure to write the same rows twice.

```bash
depwatch refresh          # re-ingest everything already known
depwatch gates            # prints each gate; exits 1 on a breach
```

### Running the schedule against your own instance

Two repository secrets, and the token is generated by Render rather than
written anywhere:

| secret | value |
| --- | --- |
| `DEPWATCH_URL` | your instance's URL |
| `DEPWATCH_REFRESH_TOKEN` | the `REFRESH_TOKEN` Render generated, from its dashboard |

## Tests

```bash
pytest       # 189 cases
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

A third job runs against Postgres. Everything else is SQLite, including the
whole unit suite, so the Postgres branches would otherwise never execute — and
the upsert picks its insert construct by dialect, which is exactly the piece
reruns and backfills depend on. That job migrates, checks for model drift
against a real Postgres schema, ingests, ingests the same packages *again* and
asserts the row counts did not move, and checks the gates read
timezone-aware timestamps correctly. SQLite returns naive datetimes from a
`timezone=True` column and Postgres returns aware ones, so half of that
comparison is unreachable locally.

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
  refresh.py       re-ingesting what is already known, one run at a time
  gates.py         freshness and quality, as pass or fail
  auth.py          one shared secret, failing closed
  ratelimit.py     a sliding window per client; /healthz exempt
  logs.py          text by default, JSON for a platform to search
  cli.py           migrate, ingest, advisories, seed, refresh, gates, runs, serve
  migrations/      alembic, shipped inside the package
  web/index.html   the dashboard, dependency-free
  sources/npm.py   registry projection and its awkward shapes
  sources/osv.py   advisory projection
```

## Next

1. A materialised score, so `/packages` stops being O(packages) per request
2. PyPI source behind the same interface
3. GitHub activity: last commit, contributors, issue responsiveness
4. dbt marts, moving the gates from Python into the transformation layer
