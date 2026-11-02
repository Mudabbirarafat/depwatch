"""OSV.dev advisory source.

OSV is a POST API rather than a GET: you send a package and it returns every
advisory affecting it. That means it does not fit the RateLimitedClient's
get_json, so this module owns its own small query helper and reuses the same
retry policy by delegating to the client's transport.

Shapes worth knowing, confirmed against the live API:

* `database_specific.severity` is the word (LOW / MODERATE / HIGH / CRITICAL)
  and is only present on GitHub-reviewed advisories;
* `severity` is a list of CVSS vectors, type CVSS_V3 or CVSS_V4;
* the fixed version lives several levels down, in
  affected[].ranges[].events[].fixed, and may be absent when no fix exists.
"""

from datetime import datetime
from typing import Any

ECOSYSTEM_OSV = {"npm": "npm", "pypi": "PyPI"}

SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "LOW": 1}


def query_payload(name: str, ecosystem: str = "npm") -> dict:
    return {"package": {"name": name, "ecosystem": ECOSYSTEM_OSV.get(ecosystem, ecosystem)}}


def _severity_word(vuln: dict) -> str | None:
    word = (vuln.get("database_specific") or {}).get("severity")
    if isinstance(word, str) and word.upper() in SEVERITY_ORDER:
        return word.upper()
    return None


def _cvss_vector(vuln: dict) -> str | None:
    for entry in vuln.get("severity") or []:
        if isinstance(entry, dict) and entry.get("score"):
            return entry["score"]
    return None


def _fixed_version(vuln: dict) -> str | None:
    """First fixed version across the affected ranges, if any is published."""
    for affected in vuln.get("affected") or []:
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if event.get("fixed"):
                    return event["fixed"]
    return None


def _published(vuln: dict) -> datetime | None:
    value = vuln.get("published")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_vuln(vuln: dict, *, package_name: str, ecosystem: str = "npm") -> dict:
    """Project one OSV record onto an advisories row."""
    return {
        "ecosystem": ecosystem,
        "package_name": package_name,
        "osv_id": vuln.get("id") or "",
        "severity": _severity_word(vuln),
        "cvss_vector": _cvss_vector(vuln),
        "summary": vuln.get("summary") or vuln.get("details"),
        "aliases": vuln.get("aliases") or [],
        "fixed_version": _fixed_version(vuln),
        "published_at": _published(vuln),
    }


def parse_response(body: Any, *, package_name: str, ecosystem: str = "npm") -> list[dict]:
    """Project a /v1/query response. An unknown package returns no vulns, which
    is a legitimate empty result rather than an error."""
    if not isinstance(body, dict):
        return []
    rows = [
        parse_vuln(v, package_name=package_name, ecosystem=ecosystem)
        for v in body.get("vulns") or []
        if isinstance(v, dict)
    ]
    # An advisory without an id cannot be deduplicated, so it is not usable.
    return [r for r in rows if r["osv_id"]]


def worst_severity(rows: list[dict]) -> str | None:
    """The most severe advisory in a set, or None when nothing is graded."""
    graded = [r["severity"] for r in rows if r.get("severity")]
    if not graded:
        return None
    return max(graded, key=lambda s: SEVERITY_ORDER.get(s, 0))
