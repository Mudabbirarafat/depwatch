"""Risk scoring.

Deliberately a pure function over plain data: no database, no network, no
clock reads except the one passed in. That makes every rule below directly
testable, and makes the score reproducible for a given input - which matters,
because a score nobody can explain is a score nobody will trust.

The weights are a judgement call, not science. They are stated here so a
reader can disagree with them specifically rather than vaguely.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from depwatch.versions import advisory_applies

# An unmaintained package is not automatically dangerous, but it is where the
# danger accumulates: no one is left to ship the fix.
STALE_AFTER = timedelta(days=365)
ABANDONED_AFTER = timedelta(days=365 * 2)

SEVERITY_POINTS = {"CRITICAL": 50, "HIGH": 35, "MODERATE": 20, "LOW": 10}

DEPRECATED_POINTS = 40
ABANDONED_POINTS = 25
STALE_POINTS = 12
SOLE_MAINTAINER_POINTS = 15
NO_LICENSE_POINTS = 10
UNFIXED_ADVISORY_POINTS = 15

# An archived repository is the only *declared* end-of-life signal available
# for a PyPI package, which has no deprecation flag at all, and it is a far
# more deliberate act than letting releases lapse. Weighted like deprecation
# because it says the same thing.
ARCHIVED_POINTS = 40

# Maintainers and contributors are not the same measurement: one is who may
# publish, the other is who writes the code. A package can have five
# publishers and one author. Worth less on its own, and it stacks with the
# maintainer rule on purpose when both are true.
SOLE_CONTRIBUTOR_POINTS = 10


@dataclass
class RiskResult:
    score: int
    band: str
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"score": self.score, "band": self.band, "reasons": self.reasons}


def band_for(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def score_package(
    package: dict,
    advisories: list[dict] | None = None,
    *,
    now: datetime | None = None,
    effective_version: str | None = None,
) -> RiskResult:
    """Score one package. Higher is worse; the scale is clamped to 0-100.

    `package` is a row-shaped dict (the keys the packages table uses) and
    `advisories` the rows for that package.
    """
    advisories = advisories or []
    now = now or datetime.now(tz=None)
    score = 0
    reasons: list[str] = []

    # Only advisories that still affect the version on offer count. Without
    # this, a well-maintained package is penalised forever for vulnerabilities
    # it fixed years ago - express, for instance, has five advisories all
    # fixed by 4.20.0 while shipping 5.3.0.
    # The version the consumer would actually install, when the caller has
    # resolved their manifest range; otherwise the package's latest.
    current_version = effective_version or package.get("latest_version")
    def applies(advisory: dict) -> bool:
        return advisory_applies(
            advisory.get("fixed_version"),
            current_version,
            advisory.get("last_affected"),
        )

    stale_advisories = [a for a in advisories if not applies(a)]
    advisories = [a for a in advisories if applies(a)]

    # Advisories: count the worst one fully, then add a smaller amount per
    # extra, so a package with six criticals outranks one with a single
    # critical without the total running away.
    graded = sorted(
        (a for a in advisories if a.get("severity") in SEVERITY_POINTS),
        key=lambda a: SEVERITY_POINTS[a["severity"]],
        reverse=True,
    )
    if graded:
        worst = graded[0]
        score += SEVERITY_POINTS[worst["severity"]]
        reasons.append(f"{worst['severity'].lower()} severity advisory {worst['osv_id']}")
        extra = len(graded) - 1
        if extra:
            score += min(extra * 5, 20)
            reasons.append(f"{extra} further graded advisor{'y' if extra == 1 else 'ies'}")

    # "No published fix" means neither a fix nor a stated upper bound. An
    # advisory that bounds itself from above has effectively been fixed for
    # anything past that bound, and counting it here was what made a 2012
    # Django advisory penalise Django 6.
    unfixed = [
        a for a in advisories
        if not a.get("fixed_version") and not a.get("last_affected")
    ]
    if unfixed:
        score += UNFIXED_ADVISORY_POINTS
        reasons.append(f"{len(unfixed)} advisory with no published fix")

    if package.get("deprecated"):
        score += DEPRECATED_POINTS
        reasons.append("package is deprecated")

    if package.get("repo_archived"):
        score += ARCHIVED_POINTS
        reasons.append("repository is archived")

    # Prefer the last commit over the last release when both are known.
    #
    # They answer different questions and the commit is the better one: a
    # package can go a year without a release while being actively worked on,
    # and can publish a version bump the week after its last real change. Only
    # one of the two is counted, so corroborating evidence is not double
    # charged - the reason says which was used.
    pushed = package.get("repo_pushed_at")
    published = package.get("last_published_at")
    activity, what = (
        (pushed, "commit") if isinstance(pushed, datetime) else (published, "release")
    )
    if isinstance(activity, datetime):
        # Compare naive-to-naive; registry timestamps carry a zone, `now` may not.
        reference = now if now.tzinfo == activity.tzinfo else now.replace(tzinfo=activity.tzinfo)
        age = reference - activity
        if age >= ABANDONED_AFTER:
            score += ABANDONED_POINTS
            reasons.append(f"no {what} in {age.days // 365} years")
        elif age >= STALE_AFTER:
            score += STALE_POINTS
            reasons.append(f"no {what} in over a year")

    maintainers = package.get("maintainers")
    if isinstance(maintainers, int) and maintainers <= 1:
        score += SOLE_MAINTAINER_POINTS
        reasons.append("single maintainer")

    contributors = package.get("repo_contributors")
    if isinstance(contributors, int) and contributors <= 1:
        score += SOLE_CONTRIBUTOR_POINTS
        reasons.append("one contributor")

    if not package.get("license"):
        score += NO_LICENSE_POINTS
        reasons.append("no licence declared")

    if stale_advisories and not advisories:
        reasons.append(
            f"{len(stale_advisories)} past advisor"
            f"{'y' if len(stale_advisories) == 1 else 'ies'}, all fixed in the current version"
        )

    score = max(0, min(score, 100))
    return RiskResult(score=score, band=band_for(score), reasons=reasons)
