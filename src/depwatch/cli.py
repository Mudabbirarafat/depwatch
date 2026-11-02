"""Command line entry point.

    depwatch init-db
    depwatch ingest express react lodash
    depwatch ingest --from-manifest package.json
    depwatch runs
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from depwatch.db import init_db, session_scope
from depwatch.ingest import ingest_advisories, ingest_npm
from depwatch.models import IngestRun


def names_from_manifest(path: Path) -> list[str]:
    """Read dependency names out of a package.json."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    names: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        names.extend((data.get(key) or {}).keys())
    return sorted(set(names))


def cmd_ingest(args: argparse.Namespace) -> int:
    names = list(args.names)
    if args.from_manifest:
        names.extend(names_from_manifest(Path(args.from_manifest)))
    names = sorted(set(names))
    if not names:
        print("nothing to ingest: pass package names or --from-manifest", file=sys.stderr)
        return 2

    summary = asyncio.run(ingest_npm(names))
    print(json.dumps(summary, indent=2))
    # A run that could not fetch anything is a failure worth a non-zero exit,
    # so a scheduled job surfaces red instead of quietly doing nothing.
    return 1 if summary["succeeded"] == 0 else 0


def cmd_advisories(args: argparse.Namespace) -> int:
    names = list(args.names)
    if args.from_manifest:
        names.extend(names_from_manifest(Path(args.from_manifest)))
    names = sorted(set(names))
    if not names:
        print("nothing to query: pass package names or --from-manifest", file=sys.stderr)
        return 2
    print(json.dumps(asyncio.run(ingest_advisories(names)), indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("depwatch.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    with session_scope() as session:
        runs = (
            session.query(IngestRun)
            .order_by(IngestRun.id.desc())
            .limit(args.limit)
            .all()
        )
        for run in runs:
            print(
                f"#{run.id:<4} {run.source:<6} {run.status:<10} "
                f"requested={run.requested:<4} ok={run.succeeded:<4} "
                f"quarantined={run.quarantined:<3} failed={run.failed:<3} "
                f"{run.started_at:%Y-%m-%d %H:%M}"
            )
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    init_db()
    print("schema created")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="depwatch")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create the schema")
    p_init.set_defaults(func=cmd_init_db)

    p_ingest = sub.add_parser("ingest", help="ingest npm packages")
    p_ingest.add_argument("names", nargs="*", help="package names")
    p_ingest.add_argument("--from-manifest", help="read names from a package.json")
    p_ingest.set_defaults(func=cmd_ingest)

    p_adv = sub.add_parser("advisories", help="pull OSV advisories for packages")
    p_adv.add_argument("names", nargs="*")
    p_adv.add_argument("--from-manifest", help="read names from a package.json")
    p_adv.set_defaults(func=cmd_advisories)

    p_serve = sub.add_parser("serve", help="run the API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_runs = sub.add_parser("runs", help="show recent ingest runs")
    p_runs.add_argument("--limit", type=int, default=10)
    p_runs.set_defaults(func=cmd_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
