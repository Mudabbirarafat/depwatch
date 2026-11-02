"""HTTP API.

The point of the whole pipeline is this endpoint: POST a manifest, get back a
risk report. Everything upstream exists so that this can answer in
milliseconds from the warehouse instead of crawling four APIs per request.

Packages the warehouse has never seen are reported as `unknown` rather than
fetched inline. A request that fans out to npm and OSV would be slow,
rate-limited and unpredictable; ingestion is a scheduled job, and saying "I
don't know about this yet" is more honest than timing out.
"""

from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from depwatch.db import session_scope
from depwatch.models import Advisory, IngestRun, Package
from depwatch.risk import score_package

app = FastAPI(
    title="depwatch",
    description="Dependency health and supply-chain risk.",
    version="0.2.0",
)

MANIFEST_KEYS = ("dependencies", "devDependencies", "peerDependencies")


class AnalyseRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    manifest: dict | None = Field(
        default=None, description="A package.json; its dependency names are read out"
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
    advisories: int
    score: int
    band: str
    reasons: list[str]


class AnalyseResponse(BaseModel):
    analysed: list[PackageReport]
    unknown: list[str]
    summary: dict


def names_from_manifest(manifest: dict) -> list[str]:
    names: list[str] = []
    for key in MANIFEST_KEYS:
        section = manifest.get(key)
        if isinstance(section, dict):
            names.extend(section.keys())
    return sorted(set(names))


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


@app.get("/healthz")
def healthz() -> dict:
    """Liveness plus the freshness numbers worth alerting on."""
    with session_scope() as session:
        packages = session.scalar(select(func.count()).select_from(Package)) or 0
        advisories = session.scalar(select(func.count()).select_from(Advisory)) or 0
        last = session.scalars(
            select(IngestRun).order_by(IngestRun.id.desc()).limit(1)
        ).first()
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
    wanted = set(request.names)
    if request.manifest:
        wanted.update(names_from_manifest(request.manifest))

    if not wanted:
        return AnalyseResponse(analysed=[], unknown=[], summary={"requested": 0})

    with session_scope() as session:
        packages = session.scalars(
            select(Package).where(
                Package.ecosystem == request.ecosystem, Package.name.in_(wanted)
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
            row = _row_to_dict(package)
            found = by_package.get(name, [])
            result = score_package(row, found)
            reports.append(
                PackageReport(
                    **row,
                    advisories=len(found),
                    score=result.score,
                    band=result.band,
                    reasons=result.reasons,
                )
            )

    reports.sort(key=lambda r: r.score, reverse=True)
    unknown = sorted(wanted - set(known))

    return AnalyseResponse(
        analysed=reports,
        unknown=unknown,
        summary={
            "requested": len(wanted),
            "analysed": len(reports),
            "unknown": len(unknown),
            "high": sum(1 for r in reports if r.band == "high"),
            "medium": sum(1 for r in reports if r.band == "medium"),
            "low": sum(1 for r in reports if r.band == "low"),
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
                {"osv_id": advisory.osv_id, "severity": advisory.severity,
                 "fixed_version": advisory.fixed_version}
            )

        rows = []
        for package in packages:
            row = _row_to_dict(package)
            result = score_package(row, by_package.get(package.name, []))
            rows.append({**row, "advisories": len(by_package.get(package.name, [])),
                         **result.as_dict()})

    rows.sort(key=lambda r: r["score"], reverse=True)
    return {"count": len(rows), "packages": rows}
