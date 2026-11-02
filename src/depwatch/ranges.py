"""Resolving an npm version range to the version you would actually install.

Why this exists: a manifest says `"express": "^4.17.1"`, and whether you are
exposed to an advisory depends on which version that resolves to - not on the
package's latest release. Scoring against latest understates risk for anyone
pinned to an older line, which is precisely the person who needs the warning.

Scope is deliberate. The full npm range grammar includes unions (`||`), hyphen
ranges, whitespace-joined comparator sets and intricate prerelease precedence.
This handles the shapes that appear in real manifests:

    1.2.3    ^1.2.3    ~1.2.3    >=1.2.3    >1.2.3    <=1.2.3    <1.2.3
    1.x      1.2.x     *         (empty)    latest

Anything else returns None, and the caller says so in the report rather than
pretending. An honest "I could not resolve this" beats a confident wrong
answer about whether you are vulnerable.
"""

import re

from depwatch.versions import parse

_COMPARATOR = re.compile(r"^(?P<op>>=|<=|>|<|=)?\s*(?P<version>.+)$")
_WILDCARD = {"", "*", "x", "X", "latest", "any"}


def _bump_minor(v: tuple[int, int, int, bool]) -> tuple[int, int, int, bool]:
    return (v[0], v[1] + 1, 0, True)


def _bump_major(v: tuple[int, int, int, bool]) -> tuple[int, int, int, bool]:
    return (v[0] + 1, 0, 0, True)


def _caret_bounds(v: tuple[int, int, int, bool]):
    """^1.2.3 allows 1.x; ^0.2.3 allows 0.2.x; ^0.0.3 allows only 0.0.3."""
    if v[0] > 0:
        return v, _bump_major(v)
    if v[1] > 0:
        return v, _bump_minor(v)
    return v, (0, 0, v[2] + 1, True)


def bounds_for(spec: str) -> tuple[tuple | None, tuple | None] | None:
    """(inclusive lower, exclusive upper) for a range, or None if unsupported.

    Either bound may be None, meaning unbounded in that direction.
    """
    if spec is None:
        return None
    text = spec.strip()

    if text in _WILDCARD:
        return (None, None)

    # Unions and hyphen ranges are out of scope; say so rather than silently
    # honouring only half of the expression. Checked before whitespace is
    # collapsed, because "1.0.0 - 2.0.0" would otherwise look like a
    # prerelease once the spaces go.
    if "||" in text or " - " in text:
        return None

    text = re.sub(r"\s+", "", text)

    # A comparator set such as ">=1.2.3 <2.0.0" survives the collapse as one
    # unparseable token, which is the honest outcome: unsupported.

    # 1.x / 1.2.x
    if re.fullmatch(r"\d+(\.\d+)?\.[xX*]", text) or re.fullmatch(r"\d+\.[xX*]", text):
        head = text.rsplit(".", 1)[0]
        parts = [int(p) for p in head.split(".")]
        if len(parts) == 1:
            lower = (parts[0], 0, 0, True)
            return lower, _bump_major(lower)
        lower = (parts[0], parts[1], 0, True)
        return lower, _bump_minor(lower)

    if text.startswith("^"):
        v = parse(text[1:])
        return _caret_bounds(v) if v else None

    if text.startswith("~"):
        v = parse(text[1:])
        return (v, _bump_minor(v)) if v else None

    match = _COMPARATOR.match(text)
    if not match:
        return None
    op, version = match["op"], parse(match["version"])
    if version is None:
        return None

    if op in (None, "="):
        # an exact pin: lower inclusive, upper exclusive just past it
        return version, (version[0], version[1], version[2] + 1, True)
    if op == ">=":
        return version, None
    if op == ">":
        return (version[0], version[1], version[2] + 1, True), None
    if op == "<=":
        return None, (version[0], version[1], version[2] + 1, True)
    if op == "<":
        return None, version
    return None


def satisfies(version: str, spec: str) -> bool | None:
    """Does `version` fall inside `spec`? None when the range is unsupported."""
    bounds = bounds_for(spec)
    parsed = parse(version)
    if bounds is None or parsed is None:
        return None
    lower, upper = bounds
    if lower is not None and parsed < lower:
        return False
    return upper is None or parsed < upper


def resolve(spec: str, available: list[str]) -> str | None:
    """The highest published release satisfying `spec`.

    Prereleases are skipped: npm will not install 5.0.0-beta.1 for `^5.0.0`
    unless you ask for it by name, and treating one as the resolved version
    would misreport what you are running.
    """
    if bounds_for(spec) is None:
        return None

    best: tuple | None = None
    best_text: str | None = None
    for candidate in available or []:
        parsed = parse(candidate)
        if parsed is None or not parsed[3]:  # skip prereleases
            continue
        if satisfies(candidate, spec) and (best is None or parsed > best):
            best, best_text = parsed, candidate
    return best_text
