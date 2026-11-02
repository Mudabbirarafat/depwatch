"""HTTP API.

The point of the whole pipeline is /analyse: POST a manifest, get back a risk
report. Everything upstream exists so that this can answer in milliseconds
from the warehouse instead of crawling two APIs per request.

Manifest ranges are resolved against the versions a package has actually
published, so the report describes the version *you* would install rather than
the package's newest release. Scoring against latest is the understated-risk
case: it tells someone pinned to express@4.17.1 that they are fine because
express 5.3.0 is clean.

Packages the warehouse has never seen are reported as `unknown` rather than
fetched inline. A request that fans out to npm and OSV would be slow,
rate-limited and unpredictable; ingestion is a scheduled job, and saying "I
have not ingested this yet" is more honest than timing out.
"""

from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from depwatch import ranges
from depwatch.db import session_scope
from depwatch.models import Advisory, IngestRun, Package
from depwatch.risk import score_package

app = FastAPI(
    title="depwatch",
    description="Dependency health and supply-chain risk.",
    version="0.3.0",
)

MANIFEST_KEYS = ("dependencies", "devDependencies", "peerDependencies")


class AnalyseRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    manifest: dict | None = Field(
        default=None, description="A package.json; dependency names and ranges are read"
    )
    ecosystem: str = "npm"


class PackageReport(BaseModel):
    name: str
    ecosystem: str
    latest_version: str | None
    license: str | None
    maintainers: int | None
    deprecated: bool
    last_published_at: datetime | None

    requested_range: str | None
    scored_version: str | None
    resolution: str

    advisories: int
    score: int
    band: str
    reasons: list[str]


class AnalyseResponse(BaseModel):
    analysed: list[PackageReport]
    unknown: list[str]
    summary: dict


def requirements_from_manifest(manifest: dict) -> dict[str, str | None]:
    """{name: range} across the dependency sections of a package.json."""
    found: dict[str, str | None] = {}
    for key in MANIFEST_KEYS:
        section = manifest.get(key)
        if isinstance(section, dict):
            for name, spec in section.items():
                found[name] = spec if isinstance(spec, str) else None
    return found


def names_from_manifest(manifest: dict) -> list[str]:
    return sorted(requirements_from_manifest(manifest))


def _row_to_dict(package: Package) -> dict:
    return {
        "name": package.name,
        "ecosystem": package.ecosystem,
        "latest_version": package.latest_version,
        "license": package.license,
        "maintainers": package.maintainers,
        "deprecated": package.deprecated,
        "last_published_at": package.last_published_at,
    }


def _resolve(spec: str | None, package: Package) -> tuple[str | None, str]:
    """(version to score against, how we got there)."""
    if not spec:
        return package.latest_version, "latest"
    resolved = ranges.resolve(spec, package.known_versions or [])
    if resolved:
        return resolved, "resolved"
    if ranges.bounds_for(spec) is None:
        return package.latest_version, "unsupported-range"
    # A supported range that matches nothing published - a typo, or a version
    # yanked from the registry.
    return package.latest_version, "no-matching-version"


@app.get("/healthz")
def healthz() -> dict:
    """Liveness plus the freshness numbers worth alerting on."""
    with session_scope() as session:
        packages = session.scalar(select(func.count()).select_from(Package)) or 0
        advisories = session.scalar(select(func.count()).select_from(Advisory)) or 0
        last = session.scalars(select(IngestRun).order_by(IngestRun.id.desc()).limit(1)).first()
        return {
            "status": "ok",
            "packages": packages,
            "advisories": advisories,
            "last_run": (
                {
                    "id": last.id,
                    "source": last.source,
                    "status": last.status,
                    "finished_at": last.finished_at.isoformat() if last.finished_at else None,
                }
                if last
                else None
            ),
        }


@app.post("/analyse", response_model=AnalyseResponse)
def analyse(request: AnalyseRequest) -> AnalyseResponse:
    requirements: dict[str, str | None] = {name: None for name in request.names}
    if request.manifest:
        requirements.update(requirements_from_manifest(request.manifest))

    if not requirements:
        return AnalyseResponse(analysed=[], unknown=[], summary={"requested": 0})

    with session_scope() as session:
        packages = session.scalars(
            select(Package).where(
                Package.ecosystem == request.ecosystem,
                Package.name.in_(requirements.keys()),
            )
        ).all()
        known = {p.name: p for p in packages}

        advisories = session.scalars(
            select(Advisory).where(
                Advisory.ecosystem == request.ecosystem,
                Advisory.package_name.in_(known.keys()),
            )
        ).all()

        by_package: dict[str, list[dict]] = {}
        for advisory in advisories:
            by_package.setdefault(advisory.package_name, []).append(
                {
                    "osv_id": advisory.osv_id,
                    "severity": advisory.severity,
                    "fixed_version": advisory.fixed_version,
                }
            )

        reports: list[PackageReport] = []
        for name, package in known.items():
            spec = requirements.get(name)
            scored_version, resolution = _resolve(spec, package)
            found = by_package.get(name, [])
            result = score_package(
                _row_to_dict(package), found, effective_version=scored_version
            )

            reasons = list(result.reasons)
            if resolution == "unsupported-range":
                reasons.append(
                    f"range {spec!r} is not supported; scored against the latest version"
                )
            elif resolution == "no-matching-version":
                reasons.append(f"no published version satisfies {spec!r}")

            reports.append(
                PackageReport(
                    **_row_to_dict(package),
                    requested_range=spec,
                    scored_version=scored_version,
                    resolution=resolution,
                    advisories=len(found),
                    score=result.score,
                    band=result.band,
                    reasons=reasons,
                )
            )

    reports.sort(key=lambda r: r.score, reverse=True)
    unknown = sorted(set(requirements) - set(known))

    return AnalyseResponse(
        analysed=reports,
        unknown=unknown,
        summary={
            "requested": len(requirements),
            "analysed": len(reports),
            "unknown": len(unknown),
            "high": sum(1 for r in reports if r.band == "high"),
            "medium": sum(1 for r in reports if r.band == "medium"),
            "low": sum(1 for r in reports if r.band == "low"),
            "unresolved_ranges": sum(
                1 for r in reports if r.resolution in {"unsupported-range", "no-matching-version"}
            ),
        },
    )


@app.get("/packages")
def list_packages(limit: int = 50) -> dict:
    """Everything known, worst first - the dashboard's backing query."""
    with session_scope() as session:
        packages = session.scalars(select(Package).limit(limit)).all()
        advisories = session.scalars(select(Advisory)).all()

        by_package: dict[str, list[dict]] = {}
        for advisory in advisories:
            by_package.setdefault(advisory.package_name, []).append(
                {
                    "osv_id": advisory.osv_id,
                    "severity": advisory.severity,
                    "fixed_version": advisory.fixed_version,
                }
            )

        rows = []
        for package in packages:
            row = _row_to_dict(package)
            found = by_package.get(package.name, [])
            result = score_package(row, found)
            rows.append({**row, "advisories": len(found), **result.as_dict()})

    rows.sort(key=lambda r: r["score"], reverse=True)
    return {"count": len(rows), "packages": rows}
