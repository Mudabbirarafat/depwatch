"""Command line entry point.

    depwatch migrate
    depwatch ingest express react lodash
    depwatch ingest --from-manifest package.json
    depwatch runs
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from depwatch import gates, logs
from depwatch.db import session_scope
from depwatch.enrich import enrich_github
from depwatch.ingest import ingest_advisories, ingest_npm
from depwatch.migrate import upgrade_to_head
from depwatch.models import IngestRun
from depwatch.refresh import refresh
from depwatch.scores import rescore
from depwatch.seed import SEED_PACKAGES, seed


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


def cmd_seed(args: argparse.Namespace) -> int:
    # Seeding does not create the schema itself - migrations are the only way
    # it gets built - so bring it up first for anyone running this standalone.
    upgrade_to_head()
    print(json.dumps(asyncio.run(seed(args.names or None)), indent=2, default=str))
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    print(json.dumps(asyncio.run(refresh(args.names or None)), indent=2, default=str))
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    summary = asyncio.run(enrich_github(args.ecosystem, limit=args.limit))
    print(json.dumps(summary, indent=2, default=str))
    # The score uses the signals this just wrote, so it has to follow.
    print(json.dumps(rescore(args.ecosystem), indent=2, default=str))
    return 0


def cmd_rescore(args: argparse.Namespace) -> int:
    print(json.dumps(rescore(args.ecosystem), indent=2, default=str))
    return 0


def cmd_gates(args: argparse.Namespace) -> int:
    """Non-zero exit on a failing gate, so a scheduler can act on it."""
    report = gates.evaluate()
    for gate in report["gates"]:
        print(f"{'PASS' if gate['passed'] else 'FAIL'}  {gate['name']:26} {gate['detail']}")
    print()
    print("all gates passed" if report["passed"] else "GATES FAILED")
    return 0 if report["passed"] else 1


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


def cmd_migrate(args: argparse.Namespace) -> int:
    upgrade_to_head()
    print("schema at head")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="depwatch")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "migrate", aliases=["init-db"], help="create or upgrade the schema to head"
    )
    p_init.set_defaults(func=cmd_migrate)

    p_ingest = sub.add_parser("ingest", help="ingest npm packages")
    p_ingest.add_argument("names", nargs="*", help="package names")
    p_ingest.add_argument("--from-manifest", help="read names from a package.json")
    p_ingest.set_defaults(func=cmd_ingest)

    p_adv = sub.add_parser("advisories", help="pull OSV advisories for packages")
    p_adv.add_argument("names", nargs="*")
    p_adv.add_argument("--from-manifest", help="read names from a package.json")
    p_adv.set_defaults(func=cmd_advisories)

    p_seed = sub.add_parser("seed", help=f"ingest a starter set ({len(SEED_PACKAGES)} packages)")
    p_seed.add_argument("names", nargs="*")
    p_seed.set_defaults(func=cmd_seed)

    p_refresh = sub.add_parser("refresh", help="re-ingest everything already known")
    p_refresh.add_argument("names", nargs="*", help="override which packages to refresh")
    p_refresh.set_defaults(func=cmd_refresh)

    p_enrich = sub.add_parser(
        "enrich", help="add GitHub signals to packages that have a repository there"
    )
    p_enrich.add_argument("--ecosystem", default=None, help="only this ecosystem")
    p_enrich.add_argument("--limit", type=int, default=None, help="stop after this many")
    p_enrich.set_defaults(func=cmd_enrich)

    p_rescore = sub.add_parser("rescore", help="recompute the materialised score")
    p_rescore.add_argument("--ecosystem", default=None, help="only this ecosystem")
    p_rescore.set_defaults(func=cmd_rescore)

    p_gates = sub.add_parser("gates", help="check freshness and quality; exits 1 on a breach")
    p_gates.set_defaults(func=cmd_gates)

    p_serve = sub.add_parser("serve", help="run the API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_runs = sub.add_parser("runs", help="show recent ingest runs")
    p_runs.add_argument("--limit", type=int, default=10)
    p_runs.set_defaults(func=cmd_runs)

    parser.add_argument(
        "--log-level", default=None, help="DEBUG, INFO, WARNING ... (env: LOG_LEVEL)"
    )
    parser.add_argument(
        "--log-format", default=None, choices=["text", "json"], help="(env: LOG_FORMAT)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Configured here rather than at import, so importing the package does not
    # reach into another program's logging setup.
    logs.configure(level=args.log_level, fmt=args.log_format)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
